"""Tests del modelo v2 (fase 3): dataset sin fuga, ratings con encogimiento, probabilidades por
línea, calibración, mercado sin margen y reglas del backtest. Sin BD."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.services import model_backtest as bt
from backend.services.market_probabilities import (
    MatchPrediction,
    mezclar,
    probabilidades_sin_margen,
    probability_for_line,
    sin_margen_power,
)
from backend.services.model_calibration_v2 import Calibracion, Platt, ajustar_platt, ece
from backend.services.model_dataset import TrainingMatch, assert_sin_fuga
from backend.services.team_ratings_model import (
    FittedRatings,
    RatingsHyper,
    fit_ratings,
    pesos_temporales,
)

INICIO = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
LIGA = "soccer_spain_la_liga"


def partido(mid: int, dia: int, home: int, away: int, hg: int, ag: int, liga: str = LIGA) -> TrainingMatch:
    return TrainingMatch(
        match_id=mid, sport_key=liga, commence_time=INICIO + timedelta(days=dia),
        home_id=home, away_id=away, home_goals=hg, away_goals=ag, goals_1h=hg // 2, goals_2h=hg - hg // 2 + ag,
    )


def liga_todos_contra_todos(goles: dict[int, int], vueltas: int = 2) -> list[TrainingMatch]:
    """Liga de 6 equipos: el local marca ``goles[local]`` y el visitante 1 (para ver ataques)."""
    equipos = sorted(goles)
    out, mid, dia = [], 0, 0
    for _ in range(vueltas):
        for h in equipos:
            for a in equipos:
                if h != a:
                    mid += 1
                    dia += 1
                    out.append(partido(mid, dia, h, a, goles[h], 1))
    return out


DATOS = liga_todos_contra_todos({1: 3, 2: 2, 3: 1, 4: 1, 5: 1, 6: 0})
CORTE = DATOS[-1].commence_time + timedelta(days=1)


class TestSinFuga:
    def test_partido_futuro_falla(self) -> None:
        with pytest.raises(ValueError, match="Fuga"):
            assert_sin_fuga(DATOS, DATOS[10].commence_time)

    def test_el_ajuste_tambien_lo_comprueba(self) -> None:
        with pytest.raises(ValueError, match="Fuga"):
            fit_ratings(DATOS, DATOS[0].commence_time, RatingsHyper())


class TestRatings:
    FIT = fit_ratings(DATOS, CORTE, RatingsHyper())

    def test_el_mas_goleador_espera_mas_goles(self) -> None:
        assert self.FIT.lambdas(LIGA, 1, 3)[0] > self.FIT.lambdas(LIGA, 2, 3)[0] > self.FIT.lambdas(LIGA, 6, 3)[0]

    def test_datos_simetricos_dan_ratings_nulos(self) -> None:
        iguales = fit_ratings(liga_todos_contra_todos(dict.fromkeys(range(1, 7), 1)), CORTE, RatingsHyper())
        assert all(abs(v) < 1e-3 for v in iguales.goles.ataque.values())
        assert all(abs(v) < 1e-3 for v in iguales.goles.defensa.values())

    def test_equipo_sin_partidos_es_la_media_de_su_liga(self) -> None:
        lh, la = self.FIT.lambdas(LIGA, 999, 998)
        g = self.FIT.goles
        assert lh == pytest.approx(math.exp(g.mu + g.liga[LIGA] + g.campo + g.campo_liga[LIGA]))
        assert la == pytest.approx(math.exp(g.mu + g.liga[LIGA]))

    def test_menos_sigma_encoge_mas(self) -> None:
        fuerte = fit_ratings(DATOS, CORTE, RatingsHyper(sigma=0.1))
        assert abs(fuerte.goles.ataque[1]) < abs(self.FIT.goles.ataque[1])

    def test_rho_dentro_de_limites(self) -> None:
        assert -0.25 <= self.FIT.rho <= 0.25

    def test_ida_y_vuelta_json(self) -> None:
        otra = FittedRatings.from_dict(self.FIT.to_dict())
        assert otra.lambdas(LIGA, 1, 2) == pytest.approx(self.FIT.lambdas(LIGA, 1, 2))
        assert otra.partidos(1) == self.FIT.partidos(1) == 20

    def test_pesos_a_la_semivida(self) -> None:
        corte = INICIO + timedelta(days=60)
        w = pesos_temporales([INICIO, corte], corte, 60.0)
        assert w[0] == pytest.approx(0.5) and w[1] == pytest.approx(1.0)
        assert list(pesos_temporales([INICIO], corte, None)) == [1.0]


PRED = MatchPrediction(1.6, 1.1, -0.08)


class TestProbabilidades:
    def p(self, mk: str, outcome: str, point: float | None = None, cal: Calibracion | None = None) -> float:
        valor = probability_for_line(PRED, mk, outcome, point, "Local FC", "Visitante CF", cal)
        assert valor is not None
        return valor

    def test_1x2_suma_uno_y_doble_oportunidad_es_la_suma(self) -> None:
        ph, pd, pa = self.p("h2h", "Local FC"), self.p("h2h", "Draw"), self.p("h2h", "Visitante CF")
        assert ph + pd + pa == pytest.approx(1.0)
        assert self.p("double_chance", "Local FC or Draw") == pytest.approx(ph + pd)
        assert self.p("double_chance", "Local FC or Visitante CF") == pytest.approx(ph + pa)

    def test_mas_y_menos_de_una_linea_media(self) -> None:
        assert self.p("totals", "Over", 2.5) + self.p("totals", "Under", 2.5) == pytest.approx(1.0)

    def test_linea_entera_deja_el_empate_fuera(self) -> None:
        assert self.p("totals", "Over", 2.0) + self.p("totals", "Under", 2.0) < 1.0

    def test_ambos_marcan_y_mitades(self) -> None:
        assert self.p("btts", "Yes") + self.p("btts", "No") == pytest.approx(1.0)
        mitades = [self.p("match_highest_scoring_half", e) for e in ("1ª mitad", "Igual", "2ª mitad")]
        assert sum(mitades) == pytest.approx(1.0)

    def test_goles_del_local(self) -> None:
        assert self.p("team_home_totals_goals", "Over", 0.5) > self.p("team_away_totals_goals", "Over", 0.5)

    def test_calibracion_que_baja_la_confianza(self) -> None:
        cal = Calibracion({"totals": Platt(0.0, 0.5)})
        bruta, calibrada = self.p("totals", "Over", 1.5), self.p("totals", "Over", 1.5, cal)
        assert 0.5 < calibrada < bruta
        assert calibrada + self.p("totals", "Under", 1.5, cal) == pytest.approx(1.0)

    def test_1x2_calibrado_sigue_sumando_uno(self) -> None:
        cal = Calibracion({"1x2": Platt(0.1, 0.7)})
        total = sum(self.p("h2h", o, cal=cal) for o in ("Local FC", "Draw", "Visitante CF"))
        assert total == pytest.approx(1.0)

    def test_mercado_no_cubierto(self) -> None:
        assert probability_for_line(PRED, "match_corners_totals", "Over", 9.5, "A", "B") is None


class TestCalibracion:
    def test_modelo_sobreconfiado_da_b_menor_que_uno(self) -> None:
        # Dice 80 % y acierta el 60 %; dice 20 % y acierta el 40 %.
        ps = [0.8] * 200 + [0.2] * 200
        ys = [1] * 120 + [0] * 80 + [1] * 80 + [0] * 120
        platt = ajustar_platt(ps, ys)
        assert platt.b < 1.0 and platt.aplicar(0.8) < 0.8

    def test_nunca_sube_la_confianza(self) -> None:
        ps = [0.6] * 200 + [0.4] * 200
        ys = [1] * 190 + [0] * 10 + [0] * 190 + [1] * 10
        assert ajustar_platt(ps, ys).b <= 1.0

    def test_pocos_sucesos_no_se_calibra(self) -> None:
        assert ajustar_platt([0.7] * 10, [1] * 10) == Platt(n=10)

    def test_ece_de_un_modelo_perfecto(self) -> None:
        assert ece([0.25] * 4, [1, 0, 0, 0]) == pytest.approx(0.0)


class TestMercadoSinMargen:
    def test_power_suma_uno_y_carga_mas_al_outsider(self) -> None:
        cuotas = [1.40, 4.50, 8.00]
        ps = sin_margen_power(cuotas)
        proporcional = [(1 / c) / sum(1 / x for x in cuotas) for c in cuotas]
        assert sum(ps) == pytest.approx(1.0)
        assert ps[0] > proporcional[0] and ps[2] < proporcional[2]

    def test_doble_oportunidad_sale_del_1x2(self) -> None:
        lineas = [("h2h", "A", None, 2.0), ("h2h", "Draw", None, 3.4), ("h2h", "B", None, 3.8),
                  ("double_chance", "A or Draw", None, 1.25), ("totals", "Over", 2.5, 1.9),
                  ("totals", "Under", 2.5, 1.9)]
        p = probabilidades_sin_margen(lineas, "A", "B")
        assert p[("double_chance", "A or Draw", None)] == pytest.approx(p[("h2h", "A", None)] + p[("h2h", "Draw", None)])
        assert p[("totals", "Over", 2.5)] == pytest.approx(0.5)

    def test_mezcla(self) -> None:
        assert mezclar(0.7, 0.5, 0.6) == pytest.approx(0.58)
        assert mezclar(0.7, None, 0.6) == 0.7


def ev(p: float, y: int, mid: int = 1, clave: str = "totals:2.5", fam: str = "totals") -> bt.Evento:
    return bt.Evento(fam, clave, mid, date(2026, 10, 20), p, y)


class TestReglasDelBacktest:
    def test_resultado_de_sucesos(self) -> None:
        m = partido(1, 0, 1, 2, 2, 1)
        assert bt.resultado("1x2:H", m) == 1 and bt.resultado("totals:2.5", m) == 1
        assert bt.resultado("team:away:1.5", m) == 0 and bt.resultado("btts", m) == 1

    def test_peso_del_mercado_cuando_el_mercado_acierta(self) -> None:
        eventos = [ev(0.9 if i % 2 else 0.1, i % 2 ^ 1, mid=i) for i in range(200)]  # el modelo falla
        cierres = {(e.match_id, e.clave): 0.2 if e.y == 0 else 0.8 for e in eventos}
        peso, curva = bt.elegir_peso_mercado(eventos, cierres)
        assert peso == 1.0 and curva[1.0] < curva[0.0]

    def test_familias_habilitadas(self) -> None:
        m = bt.Metricas
        modelo = {"totals": m(300, 0.60, 0.2, 0.03), "btts": m(300, 0.72, 0.25, 0.03), "1x2": m(50, 0.5, 0.2, 0.01)}
        base = {"totals": m(300, 0.65, 0.2, 0.0), "btts": m(300, 0.70, 0.25, 0.0), "1x2": m(50, 0.6, 0.2, 0.0)}
        assert bt.familias_habilitadas(modelo, base) == ["totals"]

    def test_puerta_de_promocion(self) -> None:
        assert bt.debe_promoverse(0.63, 0.68, 0.65) == (True, [])
        ok, motivos = bt.debe_promoverse(0.66, 0.68, 0.65)
        assert not ok and "tasa base" in motivos[0]
