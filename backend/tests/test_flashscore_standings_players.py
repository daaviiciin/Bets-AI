"""Tests de los parsers de Flashscore que sustituyen a Sofascore (sin red).

Datos recortados de respuestas reales del 2026-10-14 (LaLiga y Barcelona 3-0 Getafe).
"""

from __future__ import annotations

import pytest

from backend.scrapers.flashscore import (
    parse_league_page,
    parse_payload,
    parse_player_match_stats,
    parse_standings,
    player_photo_path,
)

# --------------------------------------------------------------- Clasificación

STANDINGS_FEED = (
    "IPI÷jNvak2f3¬IPU÷rcAkl5le-vJb36ZBF.png¬~"
    "TZ÷Clasificación¬TZS÷General¬~"
    "TR÷1¬TU÷q1¬TN÷Barcelona¬TI÷SKbpVP5K¬TM÷8¬TW÷8¬TDR÷0¬TL÷0¬TG÷34:7¬TP÷24¬~"
    "TR÷2¬TN÷Atlético de Madrid¬TI÷jaarqpLQ¬TM÷8¬TW÷6¬TDR÷1¬TL÷1¬TG÷18:8¬TP÷19¬~"
    "TR÷20¬TN÷Málaga¬TI÷25tIqYiJ¬TM÷8¬TW÷0¬TDR÷4¬TL÷4¬TG÷4:13¬TP÷4¬~"
)

LEAGUE_HTML = """
<meta name="description" content="Follow LaLiga standings 2026/2027 standings.">
<script>
  tournamentId: "QeI1Oeyi",
  tournamentStageId: "dWdJXP6U",
</script>
<script>cjs.initialFeeds = `SA÷1¬~ZA÷SPAIN: LaLiga¬~AA÷Es8BsIUP¬AD÷1791649800¬AB÷3¬CX÷Barcelona¬AF÷Getafe¬PX÷SKbpVP5K¬PY÷dboeiWOt¬AG÷3¬AH÷0¬~AA÷futuro01¬AD÷1792649800¬AB÷1¬CX÷Getafe¬AF÷Elche¬~`</script>
"""


class TestParseStandings:
    def test_extrae_una_fila_por_equipo(self):
        rows = parse_standings(parse_payload(STANDINGS_FEED))
        assert [r.team_name for r in rows] == ["Barcelona", "Atlético de Madrid", "Málaga"]

    def test_campos_del_lider(self):
        barca = parse_standings(parse_payload(STANDINGS_FEED))[0]
        assert barca.team_fs_id == "SKbpVP5K"
        assert (barca.rank, barca.matches_played, barca.wins, barca.draws, barca.losses) == (
            1, 8, 8, 0, 0,
        )
        assert (barca.goals_for, barca.goals_against, barca.points) == (34, 7, 24)

    def test_ignora_registros_sin_goles(self):
        rows = parse_standings([{"TI": "x", "TN": "Equipo", "TM": "3"}])
        assert rows == []


class TestParseLeaguePage:
    def test_ids_de_torneo_fase_y_temporada(self):
        page = parse_league_page(LEAGUE_HTML, league_name="La Liga")
        assert (page.tournament_id, page.stage_id) == ("QeI1Oeyi", "dWdJXP6U")
        assert (page.year_start, page.year_end) == (2026, 2027)

    def test_solo_partidos_jugados_con_ids_de_equipo(self):
        page = parse_league_page(LEAGUE_HTML, league_name="La Liga")
        assert [m.match_id for m in page.finished_matches] == ["Es8BsIUP"]
        m = page.finished_matches[0]
        assert (m.home_team_fs_id, m.away_team_fs_id) == ("SKbpVP5K", "dboeiWOt")
        assert (m.score_home, m.score_away) == (3, 0)

    def test_sin_ids_lanza_error(self):
        with pytest.raises(ValueError):
            parse_league_page("<html></html>", league_name="La Liga")


# ------------------------------------------------------- Stats de jugadores

def _player(
    pid: str, name: str, team: str, *, starter: bool, gk: bool = False,
    images: list[dict] | None = None,
) -> dict:
    return {
        "inBaseLineup": starter,
        "participant": {"id": pid, "name": name, "shortDisplayName": name, "images": images},
        "teamId": team,
        "position": {"isGoalkeeper": gk, "name": "Guardameta" if gk else "Extremo"},
    }


PLAYERS = {"data": {"findEventPMSById": {
    "teams": [
        {"id": "SKbpVP5K", "name": "Barcelona", "side": "HOME"},
        {"id": "dboeiWOt", "name": "Getafe", "side": "AWAY"},
    ],
    "players": [
        _player("EFo95m2P", "Gordon Anthony", "SKbpVP5K", starter=True),
        # ``images`` tal cual en data/raw/flashscore_muestras/epmsse.json.
        _player("vuAlEqes", "García Joan", "SKbpVP5K", starter=True, gk=True, images=[
            {"__typename": "Image", "path": "zJsYDqgl-URqd4A5l.png", "variantType": 15,
             "fallback": "MEN"},
            {"__typename": "Image", "path": "hjt9R2Nm-URqd4A5l.png", "variantType": 24,
             "fallback": "MEN"},
        ]),
        _player("viMb6g9n", "Gabriel Jesus", "SKbpVP5K", starter=False),
        _player("noJuega1", "Suplente Sin Minutos", "dboeiWOt", starter=False),
    ],
}}}


def _stat(player: str, type_id: str, value: str, raw: str | None = None) -> dict:
    return {"typeId": type_id, "value": value, "rawValue": raw or value, "playerId": player}


VALUES = {"data": {"findEventPMSById": {
    "stats": {"entries": [
        _stat("EFo95m2P", "MATCH_MINUTES_PLAYED", "71"),
        _stat("EFo95m2P", "GOALS", "1"),
        _stat("EFo95m2P", "SHOTS_TOTAL", "3"),
        _stat("EFo95m2P", "SHOTS_ON_TARGET", "2"),
        _stat("EFo95m2P", "EXPECTED_GOALS", "0.20", "0.1965"),
        _stat("EFo95m2P", "PASSES_ACCURACY", "87.23%"),
        _stat("EFo95m2P", "TURNOVERS", "4"),
        _stat("vuAlEqes", "MATCH_MINUTES_PLAYED", "90"),
        _stat("vuAlEqes", "SAVES_TOTAL", "2"),
        _stat("viMb6g9n", "MATCH_MINUTES_PLAYED", "19"),
        _stat("viMb6g9n", "GOALS", "1"),
        _stat("noJuega1", "MATCH_MINUTES_PLAYED", "0"),
    ]},
    "ratings": [
        {"participantId": "EFo95m2P", "value": "7.4"},
        {"participantId": "vuAlEqes", "value": "7.1"},
    ],
}}}


class TestParsePlayerMatchStats:
    def _by_name(self):
        return {p.player_name: p for p in parse_player_match_stats(PLAYERS, VALUES)}

    def test_descarta_suplentes_que_no_jugaron(self):
        assert set(self._by_name()) == {"Gordon Anthony", "García Joan", "Gabriel Jesus"}

    def test_stats_mapeadas_a_columnas_y_xg_con_rawvalue(self):
        gordon = self._by_name()["Gordon Anthony"]
        assert gordon.stats == {
            "minutes_played": 71.0, "goals": 1.0, "shots": 3.0,
            "shots_on_target": 2.0, "expected_goals": 0.1965,
        }

    def test_porcentajes_y_tipos_no_mapeados_se_ignoran(self):
        gordon = self._by_name()["Gordon Anthony"]
        assert "PASSES_ACCURACY" not in gordon.stats and "turnovers" not in gordon.stats

    def test_titular_lado_portero_y_nota(self):
        by = self._by_name()
        assert by["Gordon Anthony"].is_starter and by["Gordon Anthony"].side == "home"
        assert by["Gordon Anthony"].rating == 7.4
        assert by["García Joan"].is_goalkeeper
        assert not by["Gabriel Jesus"].is_starter and by["Gabriel Jesus"].rating is None

    def test_payloads_vacios(self):
        assert parse_player_match_stats({}, {}) == []


class TestFotoDeJugador:
    def test_la_foto_sale_de_las_images_del_participante(self):
        by = {p.player_name: p for p in parse_player_match_stats(PLAYERS, VALUES)}
        assert by["García Joan"].photo_path == "zJsYDqgl-URqd4A5l.png"

    def test_sin_images_no_hay_foto(self):
        by = {p.player_name: p for p in parse_player_match_stats(PLAYERS, VALUES)}
        assert by["Gordon Anthony"].photo_path is None

    def test_prefiere_la_variante_15(self):
        images = [{"path": "grande.png", "variantType": 24}, {"path": "peque.png", "variantType": 15}]
        assert player_photo_path(images) == "peque.png"

    def test_sin_15_usa_otra_variante(self):
        assert player_photo_path([{"path": "otra.png", "variantType": 99}]) == "otra.png"

    def test_rutas_vacias_o_con_barra_se_descartan(self):
        assert player_photo_path([{"path": "", "variantType": 15}]) is None
        assert player_photo_path([{"path": "../x.png", "variantType": 15}]) is None
        assert player_photo_path(None) is None
