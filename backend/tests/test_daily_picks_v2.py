"""Tests de la selección de picks del día v2 (``daily_picks_v2``) y de su ventana, sin BD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.services import daily_picks_v2 as dp
from backend.services.featured_daily_picks import (
    candidatas_desde_lineas,
    cuotas_viejas,
    dia_de_picks,
    ventana_del_dia,
)
from backend.services.graded_odds_service import GradedOddsLine

SAQUE = datetime(2026, 10, 25, 19, 0, tzinfo=timezone.utc)
D = Decimal


def cand(
    match_id: int,
    odds: str,
    p: str,
    *,
    market_key: str = "double_chance",
    outcome: str = "1X",
    point: str | None = None,
) -> dp.Candidata:
    return dp.Candidata(
        match_id=match_id,
        home_team=f"Local {match_id}",
        away_team=f"Visitante {match_id}",
        league="La Liga",
        commence_time=SAQUE,
        market_key=market_key,
        outcome_name=outcome,
        outcome_point=D(point) if point else None,
        offered_odds=D(odds),
        p_model=D(p),
    )


def simples(cands, **kw):
    return dp.elegir_simples(cands, odds_min=D("1.30"), odds_max=D("1.70"), **kw)


def por_ev(combo: dp.Combinada) -> tuple:
    """Puntuación de prueba (no es la regla de producción): EV y después menos patas."""
    return (combo.ev, -len(combo.patas))


class TestElegirSimples:
    def test_solo_cuotas_dentro_de_la_banda(self) -> None:
        picks, _ = simples([cand(1, "1.29", "0.90"), cand(2, "1.71", "0.70"), cand(3, "1.50", "0.70")])
        assert [p.candidata.match_id for p in picks] == [3]

    def test_una_por_partido_la_de_mayor_probabilidad(self) -> None:
        a = cand(1, "1.40", "0.72", outcome="1X")
        b = cand(1, "1.35", "0.78", market_key="totals", outcome="Over", point="1.5")
        picks, _ = simples([a, b])
        assert [p.candidata for p in picks] == [b]

    def test_ordenadas_por_probabilidad(self) -> None:
        picks, _ = simples([cand(1, "1.60", "0.65"), cand(2, "1.40", "0.75"), cand(3, "1.50", "0.70")])
        assert [p.candidata.match_id for p in picks] == [2, 3, 1]

    def test_con_valor_hasta_cinco(self) -> None:
        cands = [cand(i, "1.50", f"0.{80 - i}") for i in range(1, 8)]  # EV > 0 en todas
        picks, meta = simples(cands)
        assert len(picks) == 5
        assert not any(p.sin_valor for p in picks)
        assert meta["partidos_con_valor"] == 7

    def test_completa_hasta_tres_con_las_mejores_sin_valor(self) -> None:
        cands = [
            cand(1, "1.50", "0.70"),  # EV +0,05
            cand(2, "1.50", "0.60"),  # EV −0,10 → aviso
            cand(3, "1.50", "0.64"),  # EV −0,04
            cand(4, "1.50", "0.55"),
        ]
        picks, meta = simples(cands)
        assert [(p.candidata.match_id, p.sin_valor) for p in picks] == [(1, False), (3, True), (2, True)]
        assert picks[1].aviso is None
        assert picks[2].aviso == dp.AVISO_CUOTA_CORTA
        assert meta["simples_sin_valor"] == 2
        assert "motivo_simples" not in meta

    def test_no_amplia_la_banda_si_faltan_partidos(self) -> None:
        picks, meta = simples([cand(1, "1.50", "0.70"), cand(2, "2.10", "0.55")])
        assert len(picks) == 1
        assert meta["motivo_simples"] == "Solo 1 partido con cuota entre 1,30 y 1,70"

    def test_desempate_determinista(self) -> None:
        a, b = cand(7, "1.50", "0.70"), cand(3, "1.50", "0.70")
        assert [p.candidata.match_id for p in simples([a, b])[0]] == [3, 7]
        assert [p.candidata.match_id for p in simples([b, a])[0]] == [3, 7]


class TestCombinada:
    def test_partidos_distintos_y_cuota_total_en_banda(self) -> None:
        cands = [cand(1, "1.80", "0.60"), cand(2, "1.70", "0.62"), cand(3, "1.60", "0.66")]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev)
        assert combo is not None
        assert len({p.match_id for p in combo.patas}) == len(combo.patas)
        assert D("4") <= combo.cuota_total <= D("6")

    def test_nunca_dos_patas_del_mismo_partido(self) -> None:
        # Las dos de 2,00 del partido 1 darían 4,00 juntas, pero son del mismo partido.
        cands = [cand(1, "2.00", "0.55"), cand(1, "2.00", "0.52", outcome="X2"), cand(2, "1.30", "0.80")]
        assert dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev) is None

    def test_sin_combinada_si_no_se_llega_a_la_banda(self) -> None:
        cands = [cand(1, "1.30", "0.80"), cand(2, "1.30", "0.80")]
        assert dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev) is None

    def test_patas_fuera_de_1_25_a_2_00_no_entran(self) -> None:
        grupos = dp.grupos_para_combinada([cand(1, "2.40", "0.45"), cand(2, "1.20", "0.85")])
        assert grupos == []

    def test_una_pata_por_tramo_de_cuota_en_cada_partido(self) -> None:
        # Con "las 3 de mayor P" saldrían las tres cortas; por tramos entra una de cada nivel.
        cortas = [cand(1, f"1.3{i}", f"0.7{9 - i}", outcome=f"c{i}") for i in range(3)]
        media = cand(1, "1.60", "0.60", outcome="media")
        larga = cand(1, "1.90", "0.50", outcome="larga")
        (grupo,) = dp.grupos_para_combinada([*cortas, media, larga])
        assert [c.outcome_name for c in grupo] == ["c0", "media", "larga"]

    def test_con_tramos_se_llega_a_la_banda_4_6(self) -> None:
        cands = []
        for mid in (1, 2, 3):
            cands += [cand(mid, "1.30", "0.78", outcome="corta"), cand(mid, "1.65", "0.60", outcome="media")]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev)
        assert combo is not None and len(combo.patas) == 3

    def test_probabilidad_conjunta_y_ev(self) -> None:
        combo = dp.combinada_independiente((cand(1, "2.00", "0.50"), cand(2, "2.50", "0.40")))
        assert combo.cuota_total == D("5.00")
        assert combo.probabilidad == D("0.2000")
        assert combo.ev == D("0")
        assert not combo.sin_valor
        assert not combo.mismo_partido


def clasico(market_key: str, outcome: str, odds: str, p: str, point: str | None = None) -> dp.Candidata:
    """Línea de un FC Barcelona – Real Madrid con λ 1,8 y 1,2 (para juntar selecciones)."""
    return dp.Candidata(
        match_id=9, home_team="FC Barcelona", away_team="Real Madrid", league="La Liga",
        commence_time=SAQUE, market_key=market_key, outcome_name=outcome,
        outcome_point=D(point) if point else None, offered_odds=D(odds), p_model=D(p),
        lambda_home=D("1.8"), lambda_away=D("1.2"),
    )


class TestMismoPartido:
    GANA = clasico("h2h", "FC Barcelona", "1.80", "0.55")
    MAS_1_5 = clasico("totals", "Over", "1.30", "0.80", "1.5")
    AMBOS = clasico("btts", "Yes", "1.60", "0.62")
    NO_PIERDE = clasico("double_chance", "FC Barcelona or Draw", "1.25", "0.78")

    def test_mascara_usa_la_liquidacion(self) -> None:
        m = dp.mascara_de_pata(self.GANA)
        n = dp.TAMANO_REJILLA
        assert m is not None and m[1 * n + 0] and not m[0 * n + 0] and not m[0 * n + 1]

    def test_mercados_por_mitades_no_se_juntan(self) -> None:
        assert dp.mascara_de_pata(clasico("match_highest_scoring_half", "2ª mitad", "2.15", "0.5")) is None

    def test_selecciones_que_van_juntas_bajan_la_cuota(self) -> None:
        # Si gana el Barça casi siempre hay 2 o más goles: P(las dos) > P·P y la cuota baja.
        (combo,) = [
            c for c in dp.enumerar_combinadas_mismo_partido(
                [self.GANA, self.MAS_1_5], total_min=D("1"), total_max=D("10"),
            )
        ]
        assert combo.mismo_partido
        assert combo.cuota_total < combo.cuota_winamax
        assert combo.probabilidad > D("0.55") * D("0.80") - D("0.2")

    def test_una_que_implica_la_otra_no_se_junta(self) -> None:
        # "Gana el Barça" implica "Barça o empate": multiplicar sus cuotas no significaría nada.
        combos = list(dp.enumerar_combinadas_mismo_partido(
            [self.GANA, self.NO_PIERDE], total_min=D("1"), total_max=D("10"),
        ))
        assert combos == []

    def test_solo_se_usa_si_no_hay_combinada_de_partidos_distintos(self) -> None:
        combo = dp.elegir_combinada(
            [self.GANA, self.AMBOS, self.MAS_1_5], total_min=D("2"), total_max=D("6"),
        )
        assert combo is not None and combo.mismo_partido
        distintos = [cand(1, "1.80", "0.60"), cand(2, "1.70", "0.62")]
        combo = dp.elegir_combinada(
            [self.GANA, self.AMBOS, *distintos], total_min=D("3"), total_max=D("6"),
        )
        assert combo is not None and not combo.mismo_partido

    def test_cuota_minima_con_valor(self) -> None:
        combo = dp.combinada_independiente((cand(1, "2.00", "0.50"), cand(2, "2.00", "0.40")))
        assert combo.cuota_minima_valor == D("5")


class TestPuntuarCombinada:
    def _combo(self, prob: str, cuota: str, *, patas: int = 2, mismo: bool = False) -> dp.Combinada:
        return dp.Combinada(
            patas=tuple(cand(i, "1.50", "0.70") for i in range(patas)),
            probabilidad=D(prob), cuota_total=D(cuota), cuota_winamax=D(cuota), mismo_partido=mismo,
        )

    def test_con_valor_antes_que_sin_valor(self) -> None:
        con, sin = self._combo("0.30", "3.50"), self._combo("0.32", "3.00")  # EV +0,05 / −0,04
        assert dp.puntuar_combinada(con) > dp.puntuar_combinada(sin)

    def test_partidos_distintos_antes_que_mismo_partido(self) -> None:
        distintos, mismo = self._combo("0.30", "3.50"), self._combo("0.31", "3.40", mismo=True)
        assert dp.puntuar_combinada(distintos) > dp.puntuar_combinada(mismo)

    def test_mas_probabilidad_y_luego_menos_patas(self) -> None:
        assert dp.puntuar_combinada(self._combo("0.34", "3.10")) > dp.puntuar_combinada(self._combo("0.30", "3.50"))
        assert dp.puntuar_combinada(self._combo("0.30", "3.50")) > dp.puntuar_combinada(
            self._combo("0.30", "3.50", patas=3),
        )


class TestCongelacion:
    def test_huella_estable_e_independiente_del_orden_de_claves(self) -> None:
        a = [{"match_id": 1, "offered_odds": "1.50"}]
        b = [{"offered_odds": "1.50", "match_id": 1}]
        assert dp.huella_picks(a, None) == dp.huella_picks(b, None)
        assert dp.huella_picks(a, None) != dp.huella_picks(a, {"legs": []})

    def test_pata_compatible_con_la_liquidacion(self) -> None:
        d = dp.pata_a_dict(
            cand(1, "1.45", "0.71", market_key="totals", outcome="Over", point="1.5"),
            model_version="legacy-v1", odds_captured_at=SAQUE, sin_valor=False,
        )
        assert d["outcome_point"] == "1.5"
        assert d["model_ev"] == "0.0295"
        assert d["player_name"] is None
        assert {"match_id", "market_key", "outcome_name", "offered_odds"} <= d.keys()


class TestDiaYVentana:
    def test_el_dia_es_el_de_madrid(self) -> None:
        # 23:30 UTC del 25/10 ya es día 26 en Madrid (UTC+1 en horario de invierno).
        assert dia_de_picks(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)).isoformat() == "2026-10-26"

    def test_ventana_hasta_medianoche_de_madrid_con_margen(self) -> None:
        ahora = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
        desde, hasta = ventana_del_dia(ahora, 30)
        assert desde == ahora + timedelta(minutes=30)
        assert hasta == datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)

    def test_cuotas_viejas(self) -> None:
        ahora = SAQUE
        assert cuotas_viejas({}, ahora, 120)
        assert not cuotas_viejas({1: ahora - timedelta(minutes=30)}, ahora, 120)
        assert cuotas_viejas({1: ahora - timedelta(minutes=30), 2: ahora - timedelta(hours=3)}, ahora, 120)


def linea(**kw) -> GradedOddsLine:
    base = dict(
        match_id=1, league="La Liga", league_sport_key="soccer_spain_la_liga",
        home_team="A", away_team="B", commence_time=SAQUE, market_key="double_chance",
        outcome_name="1X", outcome_point=None, player_name=None, bookmaker_key="winamax_es",
        bookmaker_title="Winamax", offered_odds=D("1.40"), confidence="low",
        model_probability=D("0.70"), model_ev=D("-0.02"), recommendable=True,
    )
    return GradedOddsLine(**{**base, **kw})


class TestCandidatas:
    DESDE = SAQUE - timedelta(hours=2)
    HASTA = SAQUE + timedelta(hours=2)

    def _ids(self, lineas) -> list[int]:
        return [c.match_id for c in candidatas_desde_lineas(lineas, desde=self.DESDE, hasta=self.HASTA)]

    def test_entra_una_linea_normal(self) -> None:
        assert self._ids([linea()]) == [1]

    @pytest.mark.parametrize(
        "cambio",
        [
            {"model_probability": None},
            {"player_name": "Lamine Yamal", "market_key": "player_sot_alt"},
            {"market_key": "match_corners_totals"},
            {"recommendable": False},
            {"commence_time": SAQUE + timedelta(hours=3)},
        ],
    )
    def test_quedan_fuera(self, cambio: dict) -> None:
        assert self._ids([linea(**cambio)]) == []
