"""Tests del emparejado Winamax ↔ BD por hora de inicio + parecido de nombres (sin BD ni red).

Parejas reales de la jornada europea del 2026-10-15: Winamax escribe en español.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.scrapers.winamax import (
    WinamaxMatchInfo,
    parse_football_matches,
    parse_socketio_event,
)
from backend.services.team_name_matching import team_side_score
from backend.services.winamax_odds_ingester import (
    WinamaxOddsIngester,
    merge_winamax_matches,
    pick_winamax_match_by_kickoff,
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
