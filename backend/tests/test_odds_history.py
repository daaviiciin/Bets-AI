"""Tests de la lógica pura del historial de cuotas (sin BD)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.services.odds_history import (
    LastSeen,
    line_key,
    needs_new_row,
    plan_history_changes,
)

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
OVER_25 = line_key(1, 1, 10, None, "Over", Decimal("2.5"))
UNDER_25 = line_key(1, 1, 10, None, "Under", Decimal("2.5"))


class TestLineKey:
    def test_punto_leido_de_bd_coincide_con_el_del_scraper(self):
        desde_bd = line_key(1, 1, 10, None, "Over", Decimal("2.50"))
        assert desde_bd == OVER_25
        assert {desde_bd: 1}.get(OVER_25) == 1

    def test_sin_punto_queda_none(self):
        assert line_key(1, 1, 10, None, "1", None)[5] is None


class TestNeedsNewRow:
    def test_linea_nueva_abre_fila(self):
        assert needs_new_row(None, Decimal("1.85"), previous_refresh_at=None) is True

    def test_cuota_distinta_abre_fila(self):
        last = LastSeen(7, Decimal("1.85"), T0)
        assert needs_new_row(last, Decimal("1.90"), previous_refresh_at=T0) is True

    def test_misma_cuota_con_distinta_escala_no_abre_fila(self):
        last = LastSeen(7, Decimal("1.850"), T0)
        assert needs_new_row(last, Decimal("1.85"), previous_refresh_at=T0) is False

    def test_misma_cuota_que_falto_en_el_refresh_anterior_abre_fila(self):
        last = LastSeen(7, Decimal("1.85"), T0)
        anterior = T0 + timedelta(days=1)
        assert needs_new_row(last, Decimal("1.85"), previous_refresh_at=anterior) is True


class TestPlanHistoryChanges:
    def test_primera_observacion_inserta_todo(self):
        plan = plan_history_changes(
            {}, {OVER_25: Decimal("1.85"), UNDER_25: Decimal("1.95")}, observed_at=T0,
        )
        assert plan.to_insert == {OVER_25: Decimal("1.85"), UNDER_25: Decimal("1.95")}
        assert plan.to_extend == []

    def test_sin_cambios_solo_extiende(self):
        last = {
            OVER_25: LastSeen(1, Decimal("1.85"), T0),
            UNDER_25: LastSeen(2, Decimal("1.95"), T0),
        }
        plan = plan_history_changes(
            last,
            {OVER_25: Decimal("1.85"), UNDER_25: Decimal("1.95")},
            observed_at=T0 + timedelta(hours=6),
        )
        assert plan.to_insert == {}
        assert sorted(plan.to_extend) == [1, 2]

    def test_cambio_parcial_inserta_solo_la_linea_movida(self):
        last = {
            OVER_25: LastSeen(1, Decimal("1.85"), T0),
            UNDER_25: LastSeen(2, Decimal("1.95"), T0),
        }
        plan = plan_history_changes(
            last,
            {OVER_25: Decimal("1.80"), UNDER_25: Decimal("1.95")},
            observed_at=T0 + timedelta(hours=6),
        )
        assert plan.to_insert == {OVER_25: Decimal("1.80")}
        assert plan.to_extend == [2]

    def test_linea_que_desaparece_y_vuelve_igual_abre_fila_nueva(self):
        # Refresh 1 (T0): Over y Under. Refresh 2 (T0+1d): solo Under.
        # Refresh 3 (T0+2d): vuelve Over con la misma cuota.
        last = {
            OVER_25: LastSeen(1, Decimal("1.85"), T0),
            UNDER_25: LastSeen(2, Decimal("1.95"), T0 + timedelta(days=1)),
        }
        plan = plan_history_changes(
            last,
            {OVER_25: Decimal("1.85"), UNDER_25: Decimal("1.95")},
            observed_at=T0 + timedelta(days=2),
        )
        assert plan.to_insert == {OVER_25: Decimal("1.85")}
        assert plan.to_extend == [2]

    def test_refresh_manual_con_dias_de_diferencia_sigue_extendiendo(self):
        last = {OVER_25: LastSeen(1, Decimal("1.85"), T0)}
        plan = plan_history_changes(
            last, {OVER_25: Decimal("1.85")}, observed_at=T0 + timedelta(days=5),
        )
        assert plan.to_insert == {}
        assert plan.to_extend == [1]

    def test_linea_que_ya_no_se_ofrece_no_se_toca(self):
        last = {OVER_25: LastSeen(1, Decimal("1.85"), T0)}
        plan = plan_history_changes(last, {}, observed_at=T0 + timedelta(hours=6))
        assert plan.to_insert == {}
        assert plan.to_extend == []
