"""Tests del modelo de tiros a puerta de equipo y de partido (``sot_model``, bloque B6). Sin BD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backend.services import sot_model as sm
from backend.services.market_probabilities import (
    MatchPrediction,
    probabilidades_sin_margen,
    probability_for_line,
)
from backend.services.model_calibration_v2 import familia
from backend.services.model_dataset import TrainingMatch

INICIO = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
LIGA = "soccer_spain_la_liga"


def _liga(sot_por_equipo: dict[int, int], vueltas: int = 3, *, con_sot: bool = True) -> list[TrainingMatch]:
    """Todos contra todos: el local hace ``sot_por_equipo[local]`` tiros a puerta y el visitante 3."""
    equipos = sorted(sot_por_equipo)
    out, mid = [], 0
    for _ in range(vueltas):
        for h in equipos:
            for a in equipos:
                if h != a:
                    mid += 1
                    out.append(TrainingMatch(
                        match_id=mid, sport_key=LIGA, commence_time=INICIO + timedelta(days=mid),
                        home_id=h, away_id=a, home_goals=1, away_goals=1,
                        home_sot=sot_por_equipo[h] if con_sot else None,
                        away_sot=3 if con_sot else None,
                    ))
    return out


DATOS = _liga({1: 8, 2: 6, 3: 4, 4: 4, 5: 3, 6: 2})
CORTE = DATOS[-1].commence_time + timedelta(days=1)


class TestAjuste:
    def test_medias_razonables(self) -> None:
        fit = sm.fit_sot(DATOS, CORTE)
        assert fit is not None
        sh, sa = fit.medias(LIGA, 3, 4)
        media_real = np.mean([m.home_sot + m.away_sot for m in DATOS]) / 2
        assert 2.0 < sh < 8.0 and 2.0 < sa < 8.0
        assert (sh + sa) / 2 == pytest.approx(media_real, rel=0.25)

    def test_equipo_que_mas_tira_tiene_media_mayor(self) -> None:
        fit = sm.fit_sot(DATOS, CORTE)
        assert fit is not None
        assert fit.medias(LIGA, 1, 4)[0] > fit.medias(LIGA, 6, 4)[0]

    def test_equipo_sin_datos_da_la_media_de_la_liga(self) -> None:
        fit = sm.fit_sot(DATOS, CORTE)
        assert fit is not None
        sh, _ = fit.medias(LIGA, 999, 4)
        assert 3.0 < sh < 6.5

    def test_desactivado_no_ajusta(self) -> None:
        assert sm.fit_sot(DATOS, CORTE, sm.SotHyper(activo=False)) is None

    def test_sin_datos_suficientes_no_ajusta(self) -> None:
        assert sm.fit_sot(_liga({1: 5, 2: 4}, vueltas=1), CORTE) is None
        assert sm.fit_sot(_liga({1: 5, 2: 4, 3: 3, 4: 2, 5: 6, 6: 1}, con_sot=False), CORTE) is None

    def test_partido_futuro_falla(self) -> None:
        with pytest.raises(ValueError, match="Fuga"):
            sm.fit_sot(DATOS, DATOS[5].commence_time)


class TestDispersion:
    def test_sin_sobredispersion_es_poisson(self) -> None:
        mus = np.full(200, 4.0)
        assert sm.estimar_r(np.full(200, 4.0), mus) is None

    def test_sobredispersion_da_r_finito(self) -> None:
        ys = np.array([0, 9] * 100, dtype=float)
        r = sm.estimar_r(ys, np.full(200, 4.5))
        assert r is not None and r >= sm.R_MIN

    def test_binomial_negativa_con_r_enorme_es_poisson(self) -> None:
        assert sm.p_mas_de(4.2, 3.5, 1e9) == pytest.approx(sm.p_mas_de(4.2, 3.5, None), abs=1e-6)

    def test_binomial_negativa_tiene_colas_mas_gordas(self) -> None:
        assert sm.p_mas_de(4.0, 8.5, 5.0) > sm.p_mas_de(4.0, 8.5, None)

    def test_suma_de_dos_conserva_la_varianza(self) -> None:
        r = sm.r_suma(6.0, 4.0, 3.0)
        assert r is not None and 7.0 + 49.0 / r == pytest.approx(7.0 + (16.0 + 9.0) / 6.0)


class TestMercadosSot:
    PRED = MatchPrediction(1.4, 1.1, sot_home=4.8, sot_away=3.6, sot_r=8.0)

    def test_mas_y_menos_suman_uno(self) -> None:
        mas = probability_for_line(self.PRED, "match_sot_totals", "Over", 8.5, "A", "B")
        menos = probability_for_line(self.PRED, "match_sot_totals", "Under", 8.5, "A", "B")
        assert mas is not None and menos is not None
        assert mas + menos == pytest.approx(1.0)

    def test_equipo_local_usa_su_media(self) -> None:
        local = probability_for_line(self.PRED, "team_home_sot_totals", "Over", 3.5, "A", "B")
        visit = probability_for_line(self.PRED, "team_away_sot_totals", "Over", 3.5, "A", "B")
        assert local is not None and visit is not None and local > visit

    def test_sin_modelo_de_tiros_no_da_probabilidad(self) -> None:
        assert probability_for_line(MatchPrediction(1.4, 1.1), "match_sot_totals", "Over", 8.5, "A", "B") is None

    def test_linea_entera_descuenta_el_empate(self) -> None:
        mas = probability_for_line(self.PRED, "team_home_sot_totals", "Over", 5.0, "A", "B")
        menos = probability_for_line(self.PRED, "team_home_sot_totals", "Under", 5.0, "A", "B")
        assert mas is not None and menos is not None and mas + menos < 1.0

    def test_familia_sot(self) -> None:
        assert familia("team_away_sot_totals") == "sot"

    def test_mercado_sin_margen_de_tiros_a_puerta(self) -> None:
        out = probabilidades_sin_margen(
            [("match_sot_totals", "Over", 8.5, 1.85), ("match_sot_totals", "Under", 8.5, 1.85)], "A", "B",
        )
        assert out[("match_sot_totals", "Over", 8.5)] == pytest.approx(0.5)
