"""Tests del emparejado Winamax ↔ BD por hora de inicio + parecido de nombres (sin BD ni red).

Parejas reales de la jornada europea del 2026-10-15: Winamax escribe en español. También el
protocolo del socket y el catálogo por ligas (``tournament:<id>``), con respuestas simuladas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend.scrapers.winamax import (
    SocketRouteCollector,
    WinamaxMatchInfo,
    parse_football_matches,
    parse_socketio_event,
)
from backend.services.team_name_matching import team_side_score
from backend.services.winamax_odds_ingester import (
    WinamaxOddsIngester,
    merge_winamax_matches,
    needs_odds_refresh,
    pick_winamax_match_by_kickoff,
    winamax_tournament_ids,
)

T0 = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)


def wm_match(mid: str, home: str, away: str, kickoff: datetime = T0) -> WinamaxMatchInfo:
    return WinamaxMatchInfo(
        match_id=mid, title=f"{home} - {away}", home_team=home, away_team=away,
        tournament_id="10909", tournament_name="Europa League", sport_id=1, kickoff=kickoff,
        sr_tournament_id=None, home_competitor_id=None, away_competitor_id=None, more_bets=0,
    )


# Varios partidos a la misma hora, como en una jornada europea.
JORNADA = [
    wm_match("1", "Marsella", "Olympiacos"),
    wm_match("2", "Rennes", "OFI Creta"),
    wm_match("3", "Friburgo", "Jablonec"),
    wm_match("4", "SL Benfica", "Celtic Glasgow"),
    wm_match("5", "Celta Vigo", "Juventus Turín"),
]


# Recorte de la respuesta real a ["m", {"route": "calendar:24"}] del 2026-10-15.
CALENDAR_FRAME = (
    '42["m",{"calendar":{"24":{"matches":[75622076,75632894]}},"requestId":"r1",'
    '"matches":{"75622076":{"matchId":75622076,"sportId":1,"tournamentId":151677,'
    '"title":"Friburgo - Jablonec","competitor1Name":"Friburgo","competitor2Name":"Jablonec",'
    '"competitor1Id":2538,"competitor2Id":2205,"matchStart":1792090800,"moreBets":612},'
    '"75632894":{"matchId":75632894,"sportId":5,"tournamentId":2,"title":"Tenis",'
    '"competitor1Name":"A","competitor2Name":"B","matchStart":1792090800}}}]'
)


class TestCalendarioSocket:
    def test_frame_de_evento(self) -> None:
        event = parse_socketio_event(CALENDAR_FRAME)
        assert event is not None and event[0] == "m"
        assert event[1]["requestId"] == "r1"

    @pytest.mark.parametrize("frame", ["2", "3", "40", '0{"sid":"x"}', "42no-json", '42["m"]'])
    def test_frames_de_control_o_rotos_se_ignoran(self, frame: str) -> None:
        assert parse_socketio_event(frame) is None

    def test_el_parser_de_partidos_sirve_para_el_calendario(self) -> None:
        payload = parse_socketio_event(CALENDAR_FRAME)[1]
        futbol = parse_football_matches(payload)
        assert [m.title for m in futbol] == ["Friburgo - Jablonec"]  # el tenis se descarta
        assert futbol[0].tournament_id == "151677"
        assert futbol[0].kickoff == T0

    def test_unir_catalogos_no_repite_y_gana_la_home(self) -> None:
        home = [wm_match("1", "Marsella", "Olympiacos")]
        socket = [wm_match("1", "Marsella", "Olympiacos"), wm_match("3", "Friburgo", "Jablonec")]
        merged = merge_winamax_matches(home, socket)
        assert [m.match_id for m in merged] == ["1", "3"]
        assert merged[0] is home[0]


class TestDescargaDePartidos:
    def _ingester(self, workers: int, llamadas: list[str]) -> WinamaxOddsIngester:
        def fake_fetch(tournament_id: str, match_id: str) -> dict:
            llamadas.append(match_id)
            if match_id == "roto":
                raise RuntimeError("HTTP 500")
            return {"id": match_id}

        return WinamaxOddsIngester(
            session=None, fetch_match=fake_fetch, sleep_seconds=0, fetch_workers=workers,
        )

    @pytest.mark.parametrize("workers", [1, 4])
    def test_cada_partido_recibe_su_pagina_y_los_errores_no_paran_el_resto(
        self, workers: int,
    ) -> None:
        llamadas: list[str] = []
        infos = [wm_match("a", "X", "Y"), wm_match("roto", "X", "Y"), wm_match("c", "X", "Y")]
        states = self._ingester(workers, llamadas)._download_match_states(infos)
        assert states["a"] == {"id": "a"} and states["c"] == {"id": "c"}
        assert isinstance(states["roto"], RuntimeError)
        assert sorted(llamadas) == ["a", "c", "roto"]  # una sola petición por partido


class TestTeamSideScore:
    @pytest.mark.parametrize(
        ("bd", "winamax"),
        [("Lillestrom", "Lillestrøm"), ("Celtic", "Celtic Glasgow"), ("Juventus", "Juventus Turín"),
         ("Stade Rennais", "Rennes")],  # alias ya conocido
    )
    def test_mismo_club(self, bd: str, winamax: str) -> None:
        assert team_side_score(bd, winamax) == 2

    @pytest.mark.parametrize(
        ("bd", "winamax"),
        [("Sparta Prague", "Sparta Praga"), ("OFI Crete", "OFI Creta"),
         ("Olympique de Marseille", "Marsella")],
    )
    def test_indicio(self, bd: str, winamax: str) -> None:
        assert team_side_score(bd, winamax) == 1

    def test_palabras_comunes_no_cuentan(self) -> None:
        assert team_side_score("Real Madrid", "Real Sociedad") == 0
        assert team_side_score("Manchester United", "Leeds United") == 0


class TestPickWinamaxMatchByKickoff:
    @pytest.mark.parametrize(
        ("home", "away", "esperado"),
        [
            ("Olympique de Marseille", "Olympiacos Piraeus", "1"),
            ("Stade Rennais", "OFI Crete", "2"),
            ("SC Freiburg", "Jablonec", "3"),
            ("Benfica", "Celtic", "4"),
        ],
    )
    def test_empareja_nombres_en_espanol(self, home: str, away: str, esperado: str) -> None:
        found = pick_winamax_match_by_kickoff(home, away, T0, JORNADA)
        assert found is not None and found.match_id == esperado

    def test_otra_hora_no_se_empareja(self) -> None:
        assert pick_winamax_match_by_kickoff(
            "SC Freiburg", "Jablonec", T0 + timedelta(hours=2), JORNADA,
        ) is None

    def test_sin_parecido_no_se_empareja(self) -> None:
        assert pick_winamax_match_by_kickoff("Brann", "Lincoln Red Imps", T0, JORNADA) is None

    def test_empate_entre_candidatos_no_elige_ninguno(self) -> None:
        dobles = [wm_match("a", "Sparta Praga", "X"), wm_match("b", "Sparta Rotterdam", "Y")]
        assert pick_winamax_match_by_kickoff("Sparta Prague", "Z", T0, dobles) is None


class TestSocketRouteCollector:
    def _collector(self, routes: list[str]) -> SocketRouteCollector:
        ids = iter(["r1", "r2", "r3"])
        return SocketRouteCollector(routes, new_request_id=lambda: next(ids))

    def test_pide_todas_las_rutas_al_conectar_y_solo_una_vez(self) -> None:
        c = self._collector(["tournament:36", "tournament:1"])
        assert c.on_frame('0{"sid":"x"}') == []
        enviados = [parse_socketio_event(f) for f in c.on_frame("40")]
        assert enviados == [
            ("m", {"route": "tournament:36", "requestId": "r1"}),
            ("m", {"route": "tournament:1", "requestId": "r2"}),
        ]
        assert c.on_frame("40") == []

    def test_contesta_al_ping(self) -> None:
        assert self._collector(["calendar:24"]).on_frame("2") == ["3"]

    def test_cada_respuesta_va_a_su_ruta_y_las_ajenas_se_ignoran(self) -> None:
        c = self._collector(["tournament:36", "tournament:1"])
        c.on_frame("40")
        c.on_frame('42["m",{"requestId":"r2","matches":{}}]')
        c.on_frame('42["m",{"requestId":"de-otro","matches":{}}]')
        assert list(c.responses) == ["tournament:1"]
        assert c.pending == ["tournament:36"]

    def test_rutas_repetidas_se_piden_una_vez(self) -> None:
        assert len(self._collector(["tournament:36", "tournament:36"]).on_frame("40")) == 1


def wm_state(*partidos: tuple[int, int, str, datetime]) -> dict:
    """Respuesta mínima del socket: ``(matchId, tournamentId, "Local - Visitante", inicio)``."""
    matches = {}
    for mid, tid, title, start in partidos:
        home, away = title.split(" - ")
        matches[str(mid)] = {
            "matchId": mid, "sportId": 1, "tournamentId": tid, "title": title,
            "competitor1Name": home, "competitor2Name": away, "matchStart": int(start.timestamp()),
        }
    return {"matches": matches}


class TestCatalogoPorLigas:
    def _ingester(self, torneos, calendario, pedidos: list[list[int]]) -> WinamaxOddsIngester:
        def fake_tournaments(ids: list[int]) -> dict:
            pedidos.append(list(ids))
            if isinstance(torneos, Exception):
                raise torneos
            return torneos

        def fake_calendar() -> dict:
            if isinstance(calendario, Exception):
                raise calendario
            return calendario

        return WinamaxOddsIngester(
            session=None, fetch_tournaments=fake_tournaments, fetch_calendar=fake_calendar,
        )

    def test_solo_se_piden_ligas_conocidas_sin_repetir(self) -> None:
        claves = ["soccer_spain_la_liga", "soccer_epl", "soccer_spain_la_liga", None, "otra"]
        assert winamax_tournament_ids(claves) == [1, 36]

    def test_trae_partidos_de_dentro_de_varios_dias_y_une_con_el_calendario(self) -> None:
        pedidos: list[list[int]] = []
        derbi = (1, 36, "Sevilla - Real Betis", T0 + timedelta(days=5))
        ing = self._ingester(
            {"36": wm_state(derbi)}, wm_state((2, 999, "Brann - Molde", T0), derbi), pedidos,
        )
        catalogo = ing._socket_catalog({"soccer_spain_la_liga"})
        assert pedidos == [[36]]
        assert [m.match_id for m in catalogo] == ["2", "1"]  # sin repetir, por hora de inicio

    def test_si_fallan_las_ligas_queda_el_calendario(self) -> None:
        ing = self._ingester(RuntimeError("timeout"), wm_state((2, 999, "Brann - Molde", T0)), [])
        assert [m.match_id for m in ing._socket_catalog({"soccer_epl"})] == ["2"]

    def test_si_falla_el_calendario_quedan_las_ligas(self) -> None:
        leeds = (3, 1, "Leeds United - Everton", T0 + timedelta(days=3))
        ing = self._ingester({"1": wm_state(leeds)}, RuntimeError("timeout"), [])
        assert [m.match_id for m in ing._socket_catalog({"soccer_epl"})] == ["3"]

    def test_sin_ligas_conocidas_no_se_piden_torneos(self) -> None:
        pedidos: list[list[int]] = []
        self._ingester({}, wm_state(), pedidos)._socket_catalog({None, "otra"})
        assert pedidos == []

    def test_avisa_si_una_liga_con_partidos_en_bd_llega_vacia(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        ing = self._ingester({"151665": {"matches": {}}}, wm_state(), [])
        with caplog.at_level(logging.WARNING):
            ing._socket_catalog({"soccer_uefa_champs_league"})
        assert "soccer_uefa_champs_league" in caplog.text


class TestNeedsOddsRefresh:
    """Mínimos que debe cumplir cualquier criterio de frescura (margen de 15 min)."""

    def test_partido_nunca_leido_se_descarga(self) -> None:
        assert needs_odds_refresh(T0 + timedelta(days=3), None, T0, fresh_minutes=15)

    def test_leido_hace_5_min_y_dentro_de_3_dias_se_salta(self) -> None:
        leido = T0 - timedelta(minutes=5)
        assert not needs_odds_refresh(T0 + timedelta(days=3), leido, T0, fresh_minutes=15)

    def test_leido_hace_20_min_se_descarga(self) -> None:
        leido = T0 - timedelta(minutes=20)
        assert needs_odds_refresh(T0 + timedelta(hours=2), leido, T0, fresh_minutes=15)

    def test_cerca_del_inicio_el_margen_baja_a_5_min(self) -> None:
        leido = T0 - timedelta(minutes=6)
        assert needs_odds_refresh(T0 + timedelta(minutes=90), leido, T0, fresh_minutes=15)
        assert not needs_odds_refresh(T0 + timedelta(days=1), leido, T0, fresh_minutes=15)
