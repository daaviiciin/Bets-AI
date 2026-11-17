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


# ---------------------------------------------------------------------------
# B14b (contexto y líneas), B6 (exposición por tiros a puerta) y B14d (vectores)
# ---------------------------------------------------------------------------

from backend.services import player_props_model as ppm  # noqa: E402


def _fila(n: int, pid: int = 1, *, pos: str = "F", tiros: int = 1, goles: int = 0, paradas: int = 0,
          minutos: int = 90, team: int = 10, rival: int = 20) -> ppm.FilaJugador:
    return ppm.FilaJugador(
        player_id=pid, match_id=100 + n, team_id=team, is_home=True,
        fecha=INICIO + timedelta(days=7 * n), sport_key="liga", home_id=team, away_id=rival,
        minutos=minutos, titular=True, posicion=pos, tiros_puerta=tiros, goles=goles, xg=None,
        paradas=paradas,
    )


def _lam(_sk, _h, _a):
    return 1.5, 1.0


PARTIDO = ppm.PartidoProps(lambda_equipo=1.5, lambda_rival=1.0)


class TestExposicionPorTirosAPuerta:
    def test_con_tiros_esperados_se_mide_por_tiro_del_equipo(self):
        ap = Aparicion(INICIO, 90, True, 2, 0, None, None, 1.0, 1.0, sot_equipo=4.0, sot_rival=3.0)
        assert suma_ritmo([ap], "tiros_puerta") == (2.0, 4.0)

    def test_paradas_con_los_tiros_esperados_del_rival(self):
        ap = Aparicion(INICIO, 90, True, None, None, None, 3, 1.0, 1.4, sot_equipo=4.0, sot_rival=5.0)
        assert suma_ritmo([ap], "paradas") == (3.0, 5.0)

    def test_mas_tiros_esperados_del_equipo_mas_tiros_del_jugador(self):
        aps = [Aparicion(INICIO + timedelta(days=i), 90, True, 1, 0, None, None, 1.0, 1.0, 4.0, 4.0)
               for i in range(10)]
        alto = mu_partido(aps, "tiros_puerta", prior=0.25, lambda_equipo=1.0, lambda_rival=1.0,
                          sot_equipo=6.0, sot_rival=3.0, minutos=_noventa)
        bajo = mu_partido(aps, "tiros_puerta", prior=0.25, lambda_equipo=1.0, lambda_rival=1.0,
                          sot_equipo=3.0, sot_rival=3.0, minutos=_noventa)
        assert alto > bajo

    def test_sin_tiros_esperados_hoy_no_mezcla_unidades(self):
        aps = [Aparicion(INICIO, 90, True, 1, 0, None, None, 1.0, 1.0, 4.0, 4.0)]
        assert mu_partido(aps, "tiros_puerta", prior=0.25, lambda_equipo=1.0, lambda_rival=1.0,
                          minutos=_noventa) is None


class TestLineasProps:
    def test_lado_y_linea(self):
        assert ppm.lado_prop("player_sot_alt", "Abner over 0.5") == "over"
        assert ppm.lado_prop("player_sot_alt", "Abner menos de 1.5") == "under"
        assert ppm.lado_prop("player_anytime_scorer", "Edan Diop") == "over"
        assert ppm.linea_prop("player_to_score_2plus", None) == 1.5
        assert ppm.linea_prop("player_saves_alt", 2.5) == 2.5

    def test_familias(self):
        assert ppm.familia_prop("player_sot_alt") == "player_sot"
        assert ppm.familia_prop("player_to_score_2plus") == "goleador"
        assert ppm.familia_prop("h2h") is None


class TestContexto:
    def test_jugador_con_partidos_da_probabilidad(self):
        ctx = ppm.construir_contexto([_fila(i, tiros=2) for i in range(6)], _lam)
        p = ctx.probabilidad(1, "player_sot_alt", "X over 0.5", 0.5, PARTIDO)
        assert p is not None and 0.5 < p < 1.0

    def test_pocas_apariciones_sin_probabilidad(self):
        ctx = ppm.construir_contexto([_fila(i) for i in range(2)], _lam)
        assert ctx.probabilidad(1, "player_sot_alt", "X over 0.5", 0.5, PARTIDO) is None

    def test_paradas_solo_de_porteros_y_goles_no_de_porteros(self):
        filas = [_fila(i, pid=1, pos="G", tiros=0, paradas=3) for i in range(5)]
        filas += [_fila(i, pid=2, pos="F") for i in range(5)]
        ctx = ppm.construir_contexto(filas, _lam)
        assert ctx.probabilidad(1, "player_saves_alt", "X over 2.5", 2.5, PARTIDO) is not None
        assert ctx.probabilidad(2, "player_saves_alt", "X over 2.5", 2.5, PARTIDO) is None
        assert ctx.probabilidad(1, "player_anytime_scorer", "X", None, PARTIDO) is None

    def test_lado_por_su_ultimo_equipo(self):
        ctx = ppm.construir_contexto([_fila(i, team=10, rival=20) for i in range(4)], _lam)
        assert ctx.lado(1, 10, 30) == "home" and ctx.lado(1, 30, 10) == "away" and ctx.lado(1, 3, 4) is None

    def test_fila_posterior_al_corte_falla(self):
        with pytest.raises(ValueError, match="Fuga"):
            ppm.construir_contexto([_fila(5)], _lam, cutoff=INICIO + timedelta(days=1))

    def test_menos_de_linea_entera_no_se_modela(self):
        ctx = ppm.construir_contexto([_fila(i) for i in range(6)], _lam)
        assert ctx.probabilidad(1, "player_sot_alt", "X under 1", 1.0, PARTIDO) is None


class TestVectoresCondicionados:
    def test_goleador_marginal_es_el_thinning(self):
        q, lam = 0.3, 1.5
        v = ppm.vector_goleador(q, 1, 30)
        p = sum(pm._poisson_pmf(k, lam) * v[k] for k in range(30))  # noqa: SLF001
        assert p == pytest.approx(1 - math.exp(-q * lam))

    def test_dos_goles_necesitan_dos_del_equipo(self):
        v = ppm.vector_goleador(0.5, 2, 4)
        assert v[0] == v[1] == 0.0 and v[2] == pytest.approx(0.25)

    def test_tiros_a_puerta_marginal_es_su_poisson(self):
        mu_sot, mu_g, lam = 1.2, 0.4, 1.5
        v = ppm.vector_tiros_puerta(mu_sot, mu_g, lam, 0.5, 30)
        p = sum(pm._poisson_pmf(k, lam) * v[k] for k in range(30))  # noqa: SLF001
        assert p == pytest.approx(p_mas_de(mu_sot, 0.5), abs=1e-6)
        assert v[3] > v[0]

    def test_paradas_sin_sobredispersion_no_dependen_del_marcador(self):
        v = ppm.vector_paradas(3.0, 1.2, None, 2.5, 5)
        assert len(set(v)) == 1

    def test_paradas_con_sobredispersion_suben_con_los_goles_del_rival(self):
        v = ppm.vector_paradas(3.0, 1.2, 6.0, 2.5, 5)
        assert all(a < b for a, b in zip(v, v[1:]))

    def test_vector_del_contexto_indica_de_quien_son_los_goles(self):
        filas = [_fila(i, pid=1, pos="G", tiros=0, paradas=3) for i in range(5)]
        filas += [_fila(i, pid=2, pos="F", goles=1) for i in range(5)]
        ctx = ppm.construir_contexto(filas, _lam)
        assert ctx.vector_condicional(1, "player_saves_alt", "X over 2.5", 2.5, PARTIDO)[0] == "rival"
        quien, v = ctx.vector_condicional(2, "player_anytime_scorer", "X", None, PARTIDO)
        assert quien == "equipo" and v[0] == 0.0
