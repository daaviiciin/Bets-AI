"""Tests del parser de bajas de Flashscore y de la lógica pura del ingester (sin BD ni red)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.scrapers.flashscore import (
    ABSENCE_STATUS_DOUBTFUL,
    ABSENCE_STATUS_MISSING,
    FlashscoreAbsence,
    parse_missing_players,
)
from backend.services.match_absences_ingester import (
    choose_absences_batch,
    dedupe_absences,
    flashscore_id_from_external_id,
    needs_absences_refresh,
)


def _player(pid: str, name: str, reason: str | None) -> dict:
    return {
        "__typename": "LineupMissingPlayer",
        "reason": reason,
        "player": {
            "__typename": "LineupPlayer",
            "participantId": pid,
            "name": name,
            "listName": name,
            "teamName": "Italia",  # selección del jugador, no su club: se ignora
        },
    }


# Recorte de la respuesta real de ``pq_graphql?_hash=dmpe2`` para Roma - Real Madrid
# (2026-10-14). La entrada de ``unsureMissingPlayers`` es inventada: en las muestras
# reales venía vacía y asumimos que usa el mismo tipo ``LineupMissingPlayer``.
PAYLOAD = {
    "data": {
        "findEventById": {
            "__typename": "Event",
            "eventParticipants": [
                {
                    "id": "thjOn0e2",
                    "type": {"side": "HOME"},
                    "lineup": {
                        "playerImportance": None,
                        "missingPlayers": [
                            _player("lWaQWh3N", "Bah M.", "Lesión - rodilla"),
                            _player("xjK7spn1", "Svilar M.", "Lesión - dedo del pie"),
                        ],
                        "unsureMissingPlayers": [
                            _player("dMy1KKlG", "Dybala P.", "Lesión - muscular"),
                        ],
                    },
                },
                {
                    "id": "8bU7fCE8",
                    "type": {"side": "AWAY"},
                    "lineup": {
                        "playerImportance": None,
                        "missingPlayers": [
                            _player("rPco96rl", "Asencio R.", "Fractura de pierna"),
                        ],
                        "unsureMissingPlayers": [],
                    },
                },
            ],
        },
    },
}

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class TestParseMissingPlayers:
    def test_extrae_bajas_de_los_dos_equipos(self):
        absences = parse_missing_players(PAYLOAD)
        assert len(absences) == 4
        assert {a.side for a in absences} == {"home", "away"}

    def test_bajas_y_dudas_se_distinguen_por_status(self):
        by_name = {a.player_name: a for a in parse_missing_players(PAYLOAD)}
        assert by_name["Bah M."].status == ABSENCE_STATUS_MISSING
        assert by_name["Dybala P."].status == ABSENCE_STATUS_DOUBTFUL

    def test_conserva_id_y_motivo_con_tildes(self):
        bah = next(a for a in parse_missing_players(PAYLOAD) if a.player_name == "Bah M.")
        assert bah.player_external_id == "lWaQWh3N"
        assert bah.reason == "Lesión - rodilla"

    def test_motivo_vacio_queda_none(self):
        payload = {"data": {"findEventById": {"eventParticipants": [{
            "type": {"side": "HOME"},
            "lineup": {"missingPlayers": [_player("x1", "Pérez J.", "  ")]},
        }]}}}
        assert parse_missing_players(payload)[0].reason is None

    def test_sin_bajas_devuelve_lista_vacia(self):
        payload = {"data": {"findEventById": {"eventParticipants": [{
            "type": {"side": "HOME"},
            "lineup": {"missingPlayers": [], "unsureMissingPlayers": []},
        }]}}}
        assert parse_missing_players(payload) == []

    def test_payload_vacio_o_evento_inexistente_no_rompe(self):
        assert parse_missing_players({}) == []
        assert parse_missing_players({"data": {"findEventById": None}}) == []

    def test_ignora_jugadores_sin_id(self):
        payload = {"data": {"findEventById": {"eventParticipants": [{
            "type": {"side": "AWAY"},
            "lineup": {"missingPlayers": [{"reason": "Sanción", "player": {"name": "X"}}]},
        }]}}}
        assert parse_missing_players(payload) == []


class TestFlashscoreIdFromExternalId:
    def test_id_de_flashscore(self):
        assert flashscore_id_from_external_id("flashscore:4j50U1OM") == "4j50U1OM"

    def test_otras_fuentes_devuelven_none(self):
        assert flashscore_id_from_external_id("sofascore:123") is None
        assert flashscore_id_from_external_id(None) is None
        assert flashscore_id_from_external_id("flashscore:") is None


class TestNeedsAbsencesRefresh:
    def _call(self, status, commence, fetched=None, days_ahead=3):
        return needs_absences_refresh(
            status=status,
            commence_time=commence,
            absences_fetched_at=fetched,
            now=NOW,
            days_ahead=days_ahead,
        )

    def test_proximo_nunca_consultado_se_consulta(self):
        assert self._call("scheduled", NOW + timedelta(days=2)) is True

    def test_proximo_consultado_en_un_refresh_anterior_del_dia_no_se_repite(self):
        consultado_a_las_9 = NOW - timedelta(hours=6)
        assert self._call("scheduled", NOW + timedelta(days=2), fetched=consultado_a_las_9) is False

    def test_proximo_consultado_ayer_se_vuelve_a_consultar(self):
        ayer = NOW - timedelta(hours=24)
        assert self._call("scheduled", NOW + timedelta(days=2), fetched=ayer) is True

    def test_proximo_consultado_justo_hace_20_horas_se_vuelve_a_consultar(self):
        assert self._call("scheduled", NOW + timedelta(days=1), fetched=NOW - timedelta(hours=20)) is True

    def test_proximo_fuera_de_la_ventana_no_se_consulta(self):
        assert self._call("scheduled", NOW + timedelta(days=5)) is False

    def test_jugado_nunca_consultado_se_consulta(self):
        assert self._call("finished", NOW - timedelta(days=20)) is True

    def test_jugado_consultado_antes_del_inicio_se_consulta_una_vez_mas(self):
        inicio = NOW - timedelta(hours=5)
        assert self._call("finished", inicio, fetched=inicio - timedelta(hours=2)) is True

    def test_jugado_consultado_despues_del_inicio_no_se_repite(self):
        inicio = NOW - timedelta(days=1)
        assert self._call("finished", inicio, fetched=inicio + timedelta(hours=3)) is False

    def test_aplazado_no_se_consulta(self):
        assert self._call("postponed", NOW + timedelta(days=1)) is False


class TestChooseAbsencesBatch:
    def test_proximos_primero_y_el_resto_del_cupo_al_historico(self):
        assert choose_absences_batch(["p1", "p2"], ["h1", "h2", "h3"], 4) == ["p1", "p2", "h1", "h2"]

    def test_semana_europea_reparte_en_varios_refreshes(self):
        proximos = [f"p{i}" for i in range(61)]
        historico = [f"h{i}" for i in range(300)]
        # 09:00: el cupo entero para los 40 próximos más cercanos.
        primero = choose_absences_batch(proximos, historico, 40)
        assert primero == proximos[:40]
        # 15:00: los 40 ya consultados no son elegibles; entran los 21 restantes + 19 del histórico.
        segundo = choose_absences_batch(proximos[40:], historico, 40)
        assert segundo == proximos[40:] + historico[:19]


class TestDedupeAbsences:
    def test_si_sale_en_bajas_y_dudas_gana_la_baja(self):
        baja = FlashscoreAbsence("home", "p1", "A", ABSENCE_STATUS_MISSING, "Lesión")
        duda = FlashscoreAbsence("home", "p1", "A", ABSENCE_STATUS_DOUBTFUL, "Lesión")
        assert dedupe_absences([baja, duda]) == [baja]
