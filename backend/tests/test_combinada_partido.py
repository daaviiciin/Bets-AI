"""Tests de la combinada del partido con props (B14d–B14f): P conjunta, selección, puntuación,
huella y candidatas de props. Sin BD."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.models import FeaturedDailyPick
from backend.services import daily_picks_v2 as dp
from backend.services import featured_daily_picks as fdp
from backend.services import player_props_model as ppm
from backend.services.graded_odds_service import GradedOddsLine
from backend.services.model_calibration_v2 import Calibracion

SAQUE = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)
D = Decimal
LH, LA = 1.8, 1.2


def marcador(market_key: str, outcome: str, odds: str, p: str, point: str | None = None) -> dp.Candidata:
    return dp.Candidata(
        match_id=9, home_team="FC Barcelona", away_team="Real Madrid", league="La Liga",
        commence_time=SAQUE, market_key=market_key, outcome_name=outcome,
        outcome_point=D(point) if point else None, offered_odds=D(odds), p_model=D(p),
        lambda_home=D(str(LH)), lambda_away=D(str(LA)),
    )


def goleador(nombre: str, odds: str, q: float, *, lado: str = "home") -> dp.Candidata:
    lam = LH if lado == "home" else LA
    p = 1 - math.exp(-q * lam)
    return dp.Candidata(
        match_id=9, home_team="FC Barcelona", away_team="Real Madrid", league="La Liga",
        commence_time=SAQUE, market_key="player_anytime_scorer", outcome_name=nombre,
        outcome_point=None, offered_odds=D(odds), p_model=D(str(round(p, 6))),
        lambda_home=D(str(LH)), lambda_away=D(str(LA)), player_name=nombre,
        prop_condicional=(lado, ppm.vector_goleador(q, 1, 11)),
    )


class TestPesosDePata:
    def test_mercado_del_marcador_es_cero_o_uno(self) -> None:
        w = dp.pesos_de_pata(marcador("h2h", "FC Barcelona", "1.80", "0.55"))
        assert w is not None and set(w) <= {0.0, 1.0}

    def test_goleador_depende_solo_de_los_goles_de_su_equipo(self) -> None:
        w = dp.pesos_de_pata(goleador("Lewandowski", "2.10", 0.3))
        assert w is not None
        assert w[0 * 11 + 5] == 0.0  # su equipo no marca
        assert w[2 * 11 + 0] == w[2 * 11 + 7] == 1 - 0.7**2

    def test_marginal_del_goleador_en_la_rejilla_es_el_thinning(self) -> None:
        rejilla = dp.rejilla_marcadores(LH, LA)
        w = dp.pesos_de_pata(goleador("Lewandowski", "2.10", 0.3))
        p = math.fsum(r * x for r, x in zip(rejilla, w))
        assert math.isclose(p, 1 - math.exp(-0.3 * LH), rel_tol=0.03)


class TestCombinadaDelPartido:
    def test_goleador_y_gana_su_equipo_van_juntos(self) -> None:
        gana = marcador("h2h", "FC Barcelona", "1.80", "0.55")
        gol = goleador("Lewandowski", "2.10", 0.35)
        combo = dp.combinada_del_partido((gana, gol))
        assert combo is not None and combo.mismo_partido
        assert combo.probabilidad > gana.p_model * gol.p_model
        assert combo.cuota_total < combo.cuota_winamax

    def test_goleador_rival_y_gana_el_local_van_en_contra(self) -> None:
        combo = dp.combinada_del_partido(
            (marcador("h2h", "FC Barcelona", "1.80", "0.55"), goleador("Mbappé", "2.40", 0.4, lado="away")),
        )
        assert combo is not None
        assert combo.cuota_total > combo.cuota_winamax

    def test_partidos_distintos_no_valen(self) -> None:
        otro = dp.Candidata(**{**marcador("btts", "Yes", "1.70", "0.6").__dict__, "match_id": 10})
        assert dp.combinada_del_partido((marcador("h2h", "FC Barcelona", "1.8", "0.55"), otro)) is None

    def test_dos_props_del_mismo_jugador_no_valen(self) -> None:
        a = goleador("Lewandowski", "2.10", 0.35)
        b = dp.Candidata(**{**a.__dict__, "market_key": "player_sot_alt", "outcome_name": "Lewandowski over 0.5"})
        assert dp.combinada_del_partido((a, b)) is None


class TestElegirCombinadaDelPartido:
    CANDS = [
        marcador("h2h", "FC Barcelona", "1.80", "0.58"),
        marcador("btts", "Yes", "1.65", "0.60"),
        marcador("totals", "Over", "1.55", "0.68", "2.5"),
        marcador("totals", "Under", "1.60", "0.62", "2.5"),
        marcador("btts", "No", "2.10", "0.45"),
        goleador("Lewandowski", "2.05", 0.33),
    ]

    def test_respeta_la_banda_y_el_numero_de_patas(self) -> None:
        combo = dp.elegir_combinada_del_partido(self.CANDS)
        assert combo is not None
        assert dp.PARTIDO_TOTAL_MIN <= combo.cuota_total <= dp.PARTIDO_TOTAL_MAX
        assert 2 <= len(combo.patas) <= 3
        assert len({p.match_id for p in combo.patas}) == 1

    def test_nunca_menos_goles_ni_ambos_marcan_no(self) -> None:
        for combo in dp.enumerar_combinadas_del_partido(self.CANDS):
            assert not any(dp.es_menos_goles(p.market_key, p.outcome_name) for p in combo.patas)

    def test_sin_banda_alcanzable_no_hay(self) -> None:
        assert dp.elegir_combinada_del_partido(self.CANDS[:1]) is None

    def test_respeta_el_tope_de_cuota_por_pata(self) -> None:
        larga = marcador("h2h", "Real Madrid", "4.50", "0.25")
        for combo in dp.enumerar_combinadas_del_partido([*self.CANDS, larga]):
            assert larga not in combo.patas

    def test_serializa_props_como_experimentales(self) -> None:
        combo = dp.combinada_del_partido((self.CANDS[0], self.CANDS[-1]))
        assert combo is not None
        d = dp.combinada_del_partido_a_dict(combo, model_version="v2", odds_captured_at=SAQUE)
        assert d["experimental"] is True and d["match_id"] == 9
        assert [lg["experimental"] for lg in d["legs"]] == [False, True]
        assert d["legs"][1]["player_name"] == "Lewandowski"


class TestPuntuarCombinadaDelPartido:
    """Propiedades que cumpliría cualquier criterio razonable (la regla la decide el usuario)."""

    def _combo(self, p: str, odds: str = "3.00") -> dp.Combinada:
        patas = (marcador("h2h", "FC Barcelona", "1.80", "0.55"), marcador("btts", "Yes", "1.70", "0.6"))
        return dp.Combinada(patas=patas, probabilidad=D(p), cuota_total=D(odds),
                            cuota_winamax=D(odds), mismo_partido=True)

    def test_misma_cuota_mas_probabilidad_no_puntua_menos(self) -> None:
        assert dp.puntuar_combinada_del_partido(self._combo("0.40")) >= dp.puntuar_combinada_del_partido(
            self._combo("0.30"))

    def test_es_determinista(self) -> None:
        assert dp.puntuar_combinada_del_partido(self._combo("0.35")) == dp.puntuar_combinada_del_partido(
            self._combo("0.35"))


class TestHuellaConCombinadaDelPartido:
    def test_sin_combinada_del_partido_la_huella_no_cambia(self) -> None:
        picks = [{"match_id": 1}]
        assert dp.huella_picks(picks, None) == dp.huella_picks(picks, None, None)

    def test_con_combinada_del_partido_cambia(self) -> None:
        picks = [{"match_id": 1}]
        assert dp.huella_picks(picks, None) != dp.huella_picks(picks, None, {"legs": []})


def linea_prop(**kw) -> GradedOddsLine:
    base = dict(
        match_id=9, league="La Liga", league_sport_key="soccer_spain_la_liga",
        home_team="FC Barcelona", away_team="Real Madrid", commence_time=SAQUE,
        market_key="player_sot_alt", outcome_name="Lewandowski over 0.5", outcome_point=D("0.5"),
        player_name="Lewandowski", bookmaker_key="winamax_es", bookmaker_title="Winamax",
        offered_odds=D("1.60"), confidence="low", model_probability=D("0.66"), model_ev=D("0.05"),
        recommendable=True, model_version="dc-test", partidos_min=8, player_id=7,
        prop_condicional=("home", tuple([0.6] * 11)),
    )
    return GradedOddsLine(**{**base, **kw})


class _Modelo:
    tag = "dc-test"
    familias_props = frozenset({"player_sot"})
    calibracion_props = Calibracion()


class TestCandidatasProps:
    DESDE, HASTA = SAQUE - timedelta(hours=2), SAQUE + timedelta(hours=2)

    def _n(self, *lineas, modelo=_Modelo()) -> int:
        return len(fdp.candidatas_props_desde_lineas(list(lineas), desde=self.DESDE, hasta=self.HASTA,
                                                      modelo=modelo))

    def test_entra_un_prop_de_familia_habilitada(self) -> None:
        assert self._n(linea_prop()) == 1

    def test_familia_no_habilitada_fuera(self) -> None:
        assert self._n(linea_prop(market_key="player_saves_alt", outcome_name="Courtois over 2.5")) == 0

    def test_sin_modelo_v2_no_hay_props(self) -> None:
        assert self._n(linea_prop(), modelo=None) == 0

    def test_lineas_del_modelo_antiguo_o_sin_vector_fuera(self) -> None:
        assert self._n(linea_prop(model_version="legacy-v1"), linea_prop(prop_condicional=None)) == 0

    def test_equipos_con_pocos_partidos_fuera(self) -> None:
        assert self._n(linea_prop(partidos_min=2)) == 0


class TestCorreccionCombinadaDelPartido:
    def test_sin_corregir_sale_pendiente(self) -> None:
        fila = FeaturedDailyPick(picks_json=[], match_combo_json={"legs": [], "experimental": True})
        assert fdp.combinada_del_partido_corregida(fila)["result"] == "pending"

    def test_metricas_separan_props(self) -> None:
        combos = [
            {"result": "hit", "experimental": True,
             "legs": [{"market_key": "player_sot_alt", "experimental": True, "result": "hit"}]},
            {"result": "miss", "experimental": False, "legs": [{"market_key": "h2h", "result": "miss"}]},
        ]
        m = fdp.metricas_combinada_del_partido(combos)
        assert m["total"] == 2 and m["hits"] == 1 and m["misses"] == 1
        assert m["con_props"]["hits"] == 1 and m["sin_props"]["misses"] == 1
        assert m["patas_props"] == {"player_sot": {**m["patas_props"]["player_sot"], "hits": 1}}
