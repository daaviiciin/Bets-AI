from types import SimpleNamespace

from backend.services import flashscore_stats_ingester as stats_module
from backend.services.flashscore_stats_ingester import (
    FlashscoreStatsIngester,
    _matches_with_complete_stats,
    _orient_stats_to_database,
    _score_match_orientation,
)


def test_only_matches_with_both_sides_are_complete() -> None:
    rows = [
        (1, True),
        (1, False),
        (2, True),
        (3, False),
        (4, True),
        (4, False),
        (4, True),
    ]

    assert _matches_with_complete_stats(rows) == {1, 4}


def test_reversed_fixture_sides_are_reoriented_before_persisting() -> None:
    parsed = {
        "home": {"shots_on_target": 3},
        "away": {"shots_on_target": 8},
    }
    summary_home = {"yellow_cards_1h": 1}
    summary_away = {"yellow_cards_2h": 2}

    aligned, aligned_home, aligned_away = _orient_stats_to_database(
        parsed,
        summary_home,
        summary_away,
        reversed_sides=True,
    )

    assert aligned["home"]["shots_on_target"] == 8
    assert aligned["away"]["shots_on_target"] == 3
    assert aligned_home == summary_away
    assert aligned_away == summary_home


def test_limite_se_aplica_despues_de_filtrar_top5(monkeypatch) -> None:
    def partido(fs_id: str, sport_key: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=fs_id,
            external_id=f"flashscore:{fs_id}",
            league=SimpleNamespace(sport_key=sport_key),
        )

    # Orden de la BD: los más recientes primero, y los dos primeros son de Champions.
    pendientes = [
        partido("ucl1", "soccer_uefa_champs_league"),
        partido("ucl2", "soccer_uefa_champs_league"),
        partido("liga1", "soccer_spain_la_liga"),
        partido("epl1", "soccer_epl"),
    ]
    ingester = FlashscoreStatsIngester(session=None)
    monkeypatch.setattr(ingester, "_load_eligible_matches", lambda **_: pendientes)
    monkeypatch.setattr(ingester, "_load_matches_without_period_stats", lambda **_: [])
    procesados: list[str] = []
    monkeypatch.setattr(
        ingester, "_ingest_one", lambda match, fs_id, **_: procesados.append(fs_id),
    )
    monkeypatch.setattr(stats_module.time, "sleep", lambda _: None)

    result = ingester.ingest_recent(only_top5=True, limit_matches=1)

    assert result.matches_eligible == 1
    assert procesados == ["liga1"]


def test_recarga_de_mitades_va_detras_y_sin_pedir_incidencias(monkeypatch) -> None:
    def partido(fs_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=fs_id, external_id=f"flashscore:{fs_id}",
            league=SimpleNamespace(sport_key="soccer_epl"),
        )

    ingester = FlashscoreStatsIngester(session=None)
    monkeypatch.setattr(ingester, "_load_eligible_matches", lambda **_: [partido("nuevo")])
    monkeypatch.setattr(
        ingester, "_load_matches_without_period_stats", lambda **_: [partido("antiguo")],
    )
    llamadas: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        ingester, "_ingest_one",
        lambda match, fs_id, fetch_summary=True, **_: llamadas.append((fs_id, fetch_summary)),
    )
    monkeypatch.setattr(stats_module.time, "sleep", lambda _: None)

    ingester.ingest_recent(only_top5=True, limit_matches=40)

    assert llamadas == [("nuevo", True), ("antiguo", False)]


def test_score_fallback_requires_exact_score_and_close_kickoff() -> None:
    assert _score_match_orientation(7, 2, 7, 2, delta_minutes=0) is False
    assert _score_match_orientation(7, 2, 2, 7, delta_minutes=0) is True
    assert _score_match_orientation(7, 2, 1, 2, delta_minutes=0) is None
    assert _score_match_orientation(7, 2, 7, 2, delta_minutes=16) is None