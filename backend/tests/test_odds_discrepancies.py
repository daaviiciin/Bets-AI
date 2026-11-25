"""Tests del detector de discrepancias (``odds_discrepancies``) y de la ventaja de Winamax frente
al comparador (``ventaja_mercado``). Sin BD ni red: cuotas sintéticas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.services import odds_discrepancies as od
from backend.services import ventaja_mercado as vm
from backend.services.market_probabilities import sin_margen_power

AHORA = datetime(2026, 11, 21, 9, 30, tzinfo=timezone.utc)
LOCAL, VISITANTE = "Real Madrid", "Celta Vigo"
CASAS = ("bet365_es", "sportium_es", "codere_es", "bwin_es")
# 1X2 del resto del mercado: el mismo en las cuatro casas.
H2H = {"home": Decimal("2.00"), "draw": Decimal("3.40"), "away": Decimal("3.80")}


def _senales(edge: float | None, ref: str = od.REF_CONSENSO, **kw) -> od.Senales:
    return od.Senales(edge=edge, ratio_ref=(edge + 1) if edge is not None else None,
                      z_par=None, ref_kind=ref, **kw)


def _mercado(visto: datetime = AHORA, cuotas: dict[str, Decimal] = H2H) -> list[od.CuotaObservada]:
    return [
        od.CuotaObservada(casa=c, market_key="h2h", outcome=oc, line=None, price=p, seen_at=visto)
        for c in CASAS for oc, p in cuotas.items()
    ]


def _winamax(precio_local: str, visto: datetime = AHORA) -> list[vm.LineaWinamax]:
    return [
        vm.LineaWinamax("h2h", LOCAL, None, Decimal(precio_local), visto),
        vm.LineaWinamax("h2h", "Draw", None, Decimal("3.40"), visto),
        vm.LineaWinamax("h2h", VISITANTE, None, Decimal("3.80"), visto),
    ]


def _p_local_justa() -> float:
    return sin_margen_power([float(H2H[k]) for k in ("home", "draw", "away")])[0]


class TestClasificarDiscrepancia:
    def test_ventaja_del_3_pct_es_value(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.03)) == (od.Clase.VALUE, od.Confianza.MEDIA)

    def test_ventaja_del_15_pct_sigue_siendo_value(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.15))[0] == od.Clase.VALUE

    def test_por_debajo_del_3_pct_es_ruido(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.029)) == (od.Clase.RUIDO, None)

    def test_por_encima_del_15_pct_es_error_probable(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.151)) == (
            od.Clase.ERROR_PROBABLE, od.Confianza.MEDIA,
        )

    def test_con_referencia_afilada_la_confianza_es_alta(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.05, ref=od.REF_SHARP)) == (
            od.Clase.VALUE, od.Confianza.ALTA,
        )

    def test_solo_con_el_par_no_hay_value(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.05, ref=od.REF_PAR))[0] == od.Clase.RUIDO

    def test_lados_cambiados_es_error_aunque_la_ventaja_sea_pequena(self) -> None:
        assert od.clasificar_discrepancia(_senales(0.04, lado_cambiado=True)) == (
            od.Clase.ERROR_PROBABLE, od.Confianza.ALTA,
        )


class TestClaveNeutralWinamax:
    def _clave(self, mk: str, oc: str, pt: str | None = None):
        return od.clave_neutral_winamax(
            mk, oc, Decimal(pt) if pt is not None else None, LOCAL, VISITANTE,
        )

    def test_1x2_por_nombre_de_equipo(self) -> None:
        assert self._clave("h2h", LOCAL) == ("h2h", "home", None)
        assert self._clave("h2h", "Draw") == ("h2h", "draw", None)
        assert self._clave("h2h", VISITANTE) == ("h2h", "away", None)

    def test_doble_oportunidad(self) -> None:
        assert self._clave("double_chance", f"{LOCAL} or Draw") == ("double_chance", "1X", None)
        assert self._clave("double_chance", f"Draw or {VISITANTE}") == ("double_chance", "X2", None)
        assert self._clave("double_chance", f"{LOCAL} or {VISITANTE}") == (
            "double_chance", "12", None,
        )

    def test_mas_menos_con_su_linea(self) -> None:
        assert self._clave("totals", "Over", "2.50") == ("totals", "over", Decimal("2.5"))
        assert self._clave("match_first_half_totals", "Under", "0.5") == (
            "match_first_half_totals", "under", Decimal("0.5"),
        )

    def test_mas_menos_sin_linea_no_se_compara(self) -> None:
        assert self._clave("totals", "Over") is None

    def test_ambos_marcan(self) -> None:
        assert self._clave("btts", "Yes") == ("btts", "yes", None)

    def test_mercado_que_no_trae_el_comparador(self) -> None:
        assert self._clave("team_home_totals_goals", "Over", "1.5") is None
        assert self._clave("player_shots_on_target", "Over", "0.5") is None

    def test_equipo_desconocido(self) -> None:
        assert self._clave("h2h", "Getafe") is None


class TestVentajasDelPartido:
    def _ventajas(self, precio_local: str, *, visto_winamax: datetime = AHORA,
                  visto_mercado: datetime = AHORA, otras=None):
        return vm.ventajas_del_partido(
            _winamax(precio_local, visto_winamax),
            otras if otras is not None else _mercado(visto_mercado),
            home=LOCAL, away=VISITANTE, now=AHORA,
        )

    def test_winamax_por_encima_del_consenso_se_marca_como_value(self) -> None:
        out = self._ventajas("2.20")
        v = out[("h2h", LOCAL, None)]
        assert v.clase == "value" and v.casas == len(CASAS)
        assert abs(float(v.ventaja) - (2.20 * _p_local_justa() - 1)) < 1e-3
        assert abs(float(v.p_mercado) - _p_local_justa()) < 1e-3
        assert v.leido_at == AHORA

    def test_la_misma_cuota_que_el_mercado_no_se_marca(self) -> None:
        assert self._ventajas("2.00") == {}

    def test_muy_por_encima_es_error_probable(self) -> None:
        assert self._ventajas("2.60")[("h2h", LOCAL, None)].clase == "error_probable"

    def test_consenso_leido_a_destiempo_no_cuenta(self) -> None:
        # «Actualizar cuotas» relee Winamax, pero el comparador es de hace 3 h.
        assert self._ventajas("2.20", visto_mercado=AHORA - timedelta(hours=3)) == {}

    def test_con_menos_de_tres_casas_no_hay_consenso(self) -> None:
        otras = [c for c in _mercado() if c.casa in CASAS[:2]]
        assert self._ventajas("2.20", otras=otras) == {}

    def test_las_cuotas_de_winamax_del_comparador_se_ignoran(self) -> None:
        # Si Winamax.es entrara como «otra casa» con 2,20, el consenso subiría.
        otras = _mercado() + [
            od.CuotaObservada(casa=od.WINAMAX, market_key="h2h", outcome=oc, line=None,
                              price=p, seen_at=AHORA)
            for oc, p in {"home": Decimal("2.20"), **{k: H2H[k] for k in ("draw", "away")}}.items()
        ]
        assert self._ventajas("2.20", otras=otras)[("h2h", LOCAL, None)].casas == len(CASAS)

    def test_la_doble_oportunidad_se_compara_con_el_1x2_del_resto(self) -> None:
        winamax = [vm.LineaWinamax("double_chance", f"{LOCAL} or Draw", None, Decimal("1.45"), AHORA)]
        out = vm.ventajas_del_partido(winamax, _mercado(), home=LOCAL, away=VISITANTE, now=AHORA)
        p = sin_margen_power([float(H2H[k]) for k in ("home", "draw", "away")])
        v = out[("double_chance", f"{LOCAL} or Draw", None)]
        assert abs(float(v.p_mercado) - (p[0] + p[1])) < 1e-3

    def test_misma_clave_con_dos_precios_no_se_marca(self) -> None:
        # Datos guardados antes del arreglo: dos «Real Madrid» en el 1X2 (uno era el visitante).
        winamax = _winamax("2.20") + [vm.LineaWinamax("h2h", LOCAL, None, Decimal("3.25"), AHORA)]
        out = vm.ventajas_del_partido(winamax, _mercado(), home=LOCAL, away=VISITANTE, now=AHORA)
        assert ("h2h", LOCAL, None) not in out
