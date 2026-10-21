"""Tests del beneficio y el resumen de Mis apuestas (``services/saved_parlays.py``)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from backend.services.saved_parlays import beneficio_apuesta, resumen_apuestas


def _apuesta(resultado: str, importe: str | None, cuota: str) -> SimpleNamespace:
    return SimpleNamespace(
        overall_result=resultado,
        stake=Decimal(importe) if importe is not None else None,
        combined_odds=Decimal(cuota),
    )


class TestBeneficioApuesta:
    def test_ganada_cobra_importe_por_cuota_menos_lo_apostado(self) -> None:
        assert beneficio_apuesta("hit", Decimal("10"), Decimal("2.50")) == Decimal("15.00")

    def test_perdida_pierde_el_importe(self) -> None:
        assert beneficio_apuesta("miss", Decimal("10"), Decimal("2.50")) == Decimal("-10")

    def test_pendiente_no_cuenta(self) -> None:
        assert beneficio_apuesta("pending", Decimal("10"), Decimal("2.50")) is None

    def test_nula_devuelve_el_importe(self) -> None:
        assert beneficio_apuesta("void", Decimal("10"), Decimal("2.50")) == Decimal("0")

    def test_sin_importe_no_cuenta(self) -> None:
        assert beneficio_apuesta("hit", None, Decimal("2.50")) is None


class TestResumenConNulas:
    def test_nula_no_entra_en_lo_apostado(self) -> None:
        r = resumen_apuestas([_apuesta("hit", "10", "2.00"), _apuesta("void", "50", "1.90")])
        assert (r["staked"], r["profit"], r["voids"]) == (Decimal("10.00"), Decimal("10.00"), 1)
        assert r["roi_pct"] == Decimal("100.0")


class TestResumenApuestas:
    def test_porcentaje_de_acierto_solo_con_ganadas_y_perdidas(self) -> None:
        filas = [
            _apuesta("hit", "10", "2.00"),
            _apuesta("miss", "10", "1.80"),
            _apuesta("miss", "10", "3.00"),
            _apuesta("pending", "10", "1.50"),
        ]
        r = resumen_apuestas(filas)
        assert (r["hits"], r["misses"], r["pending"]) == (1, 2, 1)
        assert r["rate_pct"] == 33.3

    def test_roi_sobre_lo_apostado_en_liquidadas(self) -> None:
        # +10 (gana 10 a 2,00) − 10 (pierde 10) = 0 € sobre 20 € → ROI 0 %.
        r = resumen_apuestas([_apuesta("hit", "10", "2.00"), _apuesta("miss", "10", "1.80")])
        assert r["staked"] == Decimal("20.00")
        assert r["profit"] == Decimal("0.00")
        assert r["roi_pct"] == Decimal("0.0")

    def test_sin_apuestas_liquidadas_no_hay_porcentajes(self) -> None:
        r = resumen_apuestas([_apuesta("pending", "10", "2.00")])
        assert r["rate_pct"] is None
        assert r["roi_pct"] is None
