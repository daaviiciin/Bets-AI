"""Tests del modelo de props de jugador ajustado al rival (``player_props_model``)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import poisson_model as pm
from backend.services.player_props_model import (
    Aparicion,
    minutos_esperados,
    mu_partido,
    p_goleador,
    p_goleador_dado_goles_equipo,
    p_mas_de,
    q_thinning,
    ritmo_encogido,
    ritmos_por_posicion,
    suma_ritmo,
)

INICIO = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _ap(
    n: int = 0,
    *,
    minutos: int = 90,
    tiros_puerta: int | None = 1,
    goles: int | None = 0,
    xg: float | None = None,
    paradas: int | None = None,
    lambda_equipo: float = 1.0,
    lambda_rival: float = 1.0,
) -> Aparicion:
    return Aparicion(
        fecha=INICIO + timedelta(days=7 * n),
        minutos=minutos,
        titular=minutos >= 60,
        tiros_puerta=tiros_puerta,
        goles=goles,
        xg=xg,
        paradas=paradas,
        lambda_equipo=lambda_equipo,
        lambda_rival=lambda_rival,
    )


def _noventa(_aps) -> float:
    return 90.0


class TestRitmoPorGolEsperado:
    def test_dos_a_puerta_contra_rival_duro_valen_mas_que_contra_uno_flojo(self):
        duro = suma_ritmo([_ap(tiros_puerta=2, lambda_equipo=0.8)], "tiros_puerta")
        flojo = suma_ritmo([_ap(tiros_puerta=2, lambda_equipo=2.0)], "tiros_puerta")
        assert duro[0] / duro[1] > flojo[0] / flojo[1]

    def test_paradas_se_miden_con_el_lambda_del_rival(self):
        total, expo = suma_ritmo([_ap(paradas=4, lambda_equipo=2.0, lambda_rival=1.6)], "paradas")
        assert (total, expo) == (4.0, 1.6)

    def test_suplente_sin_minutos_no_cuenta(self):
        assert suma_ritmo([_ap(minutos=0, tiros_puerta=0)], "tiros_puerta") == (0.0, 0.0)

    def test_goleador_mezcla_goles_y_xg(self):
        total, _ = suma_ritmo([_ap(goles=1, xg=0.3)], "goles")
        assert total == pytest.approx(0.65)

    def test_sin_xg_usa_solo_goles(self):
        assert suma_ritmo([_ap(goles=2, xg=None)], "goles")[0] == 2.0


class TestEncogimiento:
    def test_sin_partidos_da_el_ritmo_de_su_posicion(self):
        assert ritmo_encogido([], "tiros_puerta", prior=0.9) == pytest.approx(0.9)

    def test_con_muchos_partidos_manda_el_jugador(self):
        aps = [_ap(i, tiros_puerta=2) for i in range(60)]
        assert ritmo_encogido(aps, "tiros_puerta", prior=0.5) == pytest.approx(2.0, abs=0.1)

    def test_ritmos_por_posicion_ignora_sin_posicion(self):
        ritmos = ritmos_por_posicion([("F", _ap(tiros_puerta=2)), (None, _ap(tiros_puerta=9))])
        assert ritmos[("F", "tiros_puerta")] == pytest.approx(2.0)
        assert all(pos == "F" for pos, _ in ritmos)


class TestMuPartido:
    def test_mas_lambda_de_su_equipo_mas_tiros_esperados(self):
        aps = [_ap(i, tiros_puerta=1) for i in range(10)]
        facil = mu_partido(aps, "tiros_puerta", prior=1.0, lambda_equipo=2.0,
                           lambda_rival=1.0, minutos=_noventa)
        dificil = mu_partido(aps, "tiros_puerta", prior=1.0, lambda_equipo=0.8,
                             lambda_rival=1.0, minutos=_noventa)
        assert facil == pytest.approx(2.0) and dificil == pytest.approx(0.8)

    def test_sin_minutos_esperados_no_hay_mu(self):
        assert mu_partido([], "tiros_puerta", prior=1.0, lambda_equipo=1.0, lambda_rival=1.0,
                          minutos=lambda _a: None) is None

    def test_budimir_al_menos_un_tiro_a_puerta(self):
        # 15 a puerta en ~9 partidos completos con su equipo a λ ≈ 1,2; hoy λ = 1,0.
        aps = [_ap(i, tiros_puerta=t, lambda_equipo=1.2)
               for i, t in enumerate([2, 1, 5, 0, 1, 2, 1, 1, 2])]
        mu = mu_partido(aps, "tiros_puerta", prior=0.85, lambda_equipo=1.0,
                        lambda_rival=1.4, minutos=_noventa)
        assert 0.70 < p_mas_de(mu, 0.5) < 0.80


class TestMinutosEsperados:
    def test_titular_fijo_da_90(self):
        assert minutos_esperados([_ap(i) for i in range(8)]) == pytest.approx(90.0)

    def test_sin_apariciones_con_minutos_no_hay_estimacion(self):
        assert minutos_esperados([_ap(minutos=0)]) is None
        assert minutos_esperados([]) is None

    def test_los_ceros_no_cuentan(self):
        aps = [_ap(0, minutos=90), _ap(1, minutos=0), _ap(2, minutos=90)]
        assert minutos_esperados(aps) == pytest.approx(90.0)

    def test_pesan_mas_las_recientes(self):
        pierde_puesto = [_ap(i, minutos=m) for i, m in enumerate([90, 90, 90, 25, 25, 25])]
        gana_puesto = [_ap(i, minutos=m) for i, m in enumerate([25, 25, 25, 90, 90, 90])]
        assert minutos_esperados(pierde_puesto) < 57.5 < minutos_esperados(gana_puesto)

    def test_solo_mira_las_ultimas_seis(self):
        aps = [_ap(i, minutos=10) for i in range(20)] + [_ap(20 + i) for i in range(6)]
        assert minutos_esperados(aps) == pytest.approx(90.0)

    def test_suplente_de_pocos_minutos_tiene_suelo(self):
        assert minutos_esperados([_ap(i, minutos=5) for i in range(5)]) == 15.0


class TestProbabilidades:
    def test_p_mas_de_media_linea(self):
        assert p_mas_de(1.0, 0.5) == pytest.approx(1 - math.exp(-1.0))

    def test_goleador_es_al_menos_un_gol(self):
        assert p_goleador(0.4) == pytest.approx(1 - math.exp(-0.4))

    def test_thinning_coincide_con_el_poisson_del_jugador(self):
        lam, q = 1.4, 0.3
        suma = sum(
            pm._poisson_pmf(k, lam) * p_goleador_dado_goles_equipo(q, k)  # noqa: SLF001
            for k in range(30)
        )
        assert suma == pytest.approx(1 - math.exp(-q * lam))

    def test_si_su_equipo_no_marca_el_no_marca(self):
        assert p_goleador_dado_goles_equipo(0.4, 0) == 0.0

    def test_q_no_pasa_de_uno(self):
        assert q_thinning(3.0, 1.0) == 1.0
