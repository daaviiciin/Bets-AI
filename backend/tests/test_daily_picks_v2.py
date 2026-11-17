"""Tests de la selección de picks del día v2 (``daily_picks_v2``) y de su ventana, sin BD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.services import daily_picks_v2 as dp
from backend.services import featured_daily_picks as fdp
from backend.services.featured_daily_picks import (
    ReglasModelo,
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
    """Simples sin el tope por grupo de mercado (las ``cand`` son todas doble oportunidad)."""
    kw.setdefault("max_por_grupo", None)
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

    def test_como_mucho_dos_simples_del_mismo_grupo(self) -> None:
        # Los goles tienen más P en los cuatro partidos, pero solo entran dos; en los otros dos
        # se toma su mejor línea de otro grupo (doble oportunidad y 1X2).
        cands = [
            cand(i, "1.40", f"0.8{i}", market_key="totals", outcome="Over", point="2.5")
            for i in range(1, 5)
        ]
        cands += [cand(1, "1.45", "0.70"), cand(2, "1.50", "0.68", market_key="h2h", outcome="Local 2")]
        picks, _ = simples(cands, max_por_grupo=2, max_simples=5, min_simples=5)
        elegidas = {(p.candidata.match_id, p.candidata.market_key) for p in picks}
        assert elegidas == {(4, "totals"), (3, "totals"), (2, "h2h"), (1, "double_chance")}

    def test_el_tope_por_grupo_esta_activo_por_defecto(self) -> None:
        cands = [cand(i, "1.40", "0.80", market_key="totals", outcome="Over", point="2.5") for i in (1, 2, 3)]
        picks, _ = dp.elegir_simples(cands, odds_min=D("1.30"), odds_max=D("1.70"))
        assert len(picks) == dp.MAX_SIMPLES_POR_GRUPO

    def test_desempate_determinista(self) -> None:
        a, b = cand(7, "1.50", "0.70"), cand(3, "1.50", "0.70")
        assert [p.candidata.match_id for p in simples([a, b])[0]] == [3, 7]
        assert [p.candidata.match_id for p in simples([b, a])[0]] == [3, 7]


class TestCombinada:
    def test_partidos_distintos_y_cuota_total_en_banda(self) -> None:
        cands = [cand(i, "1.45", "0.70") for i in range(1, 6)]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev)
        assert combo is not None
        assert len({p.match_id for p in combo.patas}) == len(combo.patas)
        assert D("4") <= combo.cuota_total <= D("6")

    def test_nunca_dos_patas_del_mismo_partido(self) -> None:
        # 1,60 × 1,60 × 1,60 = 4,10, pero dos de ellas son del partido 1.
        cands = [
            cand(1, "1.60", "0.62"), cand(1, "1.60", "0.60", market_key="h2h", outcome="Local 1"),
            cand(2, "1.60", "0.62"),
        ]
        assert dp.elegir_combinada(
            cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev, permitir_mismo_partido=False,
        ) is None

    def test_sin_combinada_si_no_se_llega_a_la_banda(self) -> None:
        cands = [cand(1, "1.30", "0.80"), cand(2, "1.30", "0.80"), cand(3, "1.30", "0.80")]
        assert dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev) is None

    def test_minimo_tres_patas(self) -> None:
        cands = [cand(1, "1.60", "0.62"), cand(2, "1.60", "0.62")]
        assert dp.elegir_combinada(cands, total_min=D("2"), total_max=D("6"), puntuar=por_ev) is None

    def test_patas_fuera_de_1_18_a_1_60_no_entran(self) -> None:
        grupos = dp.grupos_para_combinada([cand(1, "1.70", "0.55"), cand(2, "1.15", "0.85")])
        assert grupos == []

    def test_una_pata_por_grupo_de_mercado_en_cada_partido(self) -> None:
        # Los goles tienen más P, pero la doble oportunidad y el 1X2 también entran como opción.
        goles = [
            cand(1, "1.30", "0.80", market_key="totals", outcome="Over", point="1.5"),
            cand(1, "1.35", "0.78", market_key="btts", outcome="Yes"),
        ]
        doble = cand(1, "1.25", "0.75")
        local = cand(1, "1.55", "0.60", market_key="h2h", outcome="Local 1")
        (grupo,) = dp.grupos_para_combinada([*goles, doble, local])
        assert [c.market_key for c in grupo] == ["totals", "double_chance", "h2h"]

    def test_con_cinco_patas_cortas_se_llega_a_la_banda(self) -> None:
        cands = [cand(i, "1.35", "0.78") for i in range(1, 6)]  # 1,35⁵ ≈ 4,48
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev)
        assert combo is not None
        assert len(combo.patas) == 5

    def test_una_simple_no_se_repite_en_la_combinada(self) -> None:
        simple = cand(1, "1.45", "0.70", market_key="h2h", outcome="Local 1")
        otro_mercado = cand(1, "1.25", "0.80")  # Mismo partido, doble oportunidad: sí puede ir.
        quedan = dp.sin_selecciones([simple, otro_mercado, cand(2, "1.45", "0.70")], [simple])
        assert [(c.match_id, c.market_key) for c in quedan] == [(1, "double_chance"), (2, "double_chance")]

    def test_la_misma_seleccion_con_otra_cuota_tambien_se_quita(self) -> None:
        simple = cand(1, "1.45", "0.70", market_key="totals", outcome="Over", point="2.5")
        igual = cand(1, "1.50", "0.69", market_key="totals", outcome="Over", point="2.5")
        otra_linea = cand(1, "1.20", "0.85", market_key="totals", outcome="Over", point="1.5")
        assert dp.sin_selecciones([igual, otra_linea], [simple]) == [otra_linea]

    def test_entra_la_pata_larga_aunque_la_corta_tenga_mas_probabilidad(self) -> None:
        # Con solo las cortas (1,27⁴ ≈ 2,6) no se llegaría a 4; con las largas sí.
        cands = []
        for i in range(1, 5):
            cands += [
                cand(i, "1.27", "0.76", market_key="totals", outcome="Over", point="1.5"),
                cand(i, "1.50", "0.62", market_key="totals", outcome="Over", point="2.5"),
            ]
        (grupo, *_) = dp.grupos_para_combinada(cands)
        assert [str(c.offered_odds) for c in grupo] == ["1.27", "1.50"]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev)
        assert combo is not None
        assert not combo.mismo_partido

    def test_prefiere_la_combinada_con_doble_oportunidad(self) -> None:
        # Sin perfil ganarían los goles (más P); con perfil entra al menos una doble oportunidad
        # y como mucho dos patas de goles.
        cands = []
        for i in range(1, 5):
            cands += [
                cand(i, "1.45", "0.74", market_key="totals", outcome="Over", point="1.5"),
                cand(i, "1.45", "0.70"),
            ]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"))
        assert combo is not None
        assert dp.encaja_con_perfil(combo)

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
    GANA = clasico("h2h", "FC Barcelona", "1.55", "0.60")
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
                [self.GANA, self.MAS_1_5], total_min=D("1"), total_max=D("10"), min_patas=2,
            )
        ]
        assert combo.mismo_partido
        assert combo.cuota_total < combo.cuota_winamax
        assert combo.probabilidad > D("0.60") * D("0.80") - D("0.2")

    def test_una_que_implica_la_otra_no_se_junta(self) -> None:
        # "Gana el Barça" implica "Barça o empate": multiplicar sus cuotas no significaría nada.
        combos = list(dp.enumerar_combinadas_mismo_partido(
            [self.GANA, self.NO_PIERDE], total_min=D("1"), total_max=D("10"), min_patas=2,
        ))
        assert combos == []

    def test_solo_se_usa_si_no_hay_combinada_de_partidos_distintos(self) -> None:
        # Con solo dos partidos no hay 3 patas de partidos distintos: se junta en el clásico.
        combo = dp.elegir_combinada(
            [self.GANA, self.MAS_1_5, cand(1, "1.45", "0.70")], total_min=D("2"), total_max=D("6"),
        )
        assert combo is not None and combo.mismo_partido
        distintos = [cand(1, "1.45", "0.70"), cand(2, "1.45", "0.70")]
        combo = dp.elegir_combinada(
            [self.GANA, self.MAS_1_5, *distintos], total_min=D("2"), total_max=D("6"),
        )
        assert combo is not None and not combo.mismo_partido

    def test_cuota_minima_con_valor(self) -> None:
        combo = dp.combinada_independiente((cand(1, "2.00", "0.50"), cand(2, "2.00", "0.40")))
        assert combo.cuota_minima_valor == D("5")


class TestPuntuarCombinada:
    def _combo(
        self, prob: str, cuota: str, *, patas: int = 2, mismo: bool = False,
        mercados: tuple[str, ...] | None = None,
    ) -> dp.Combinada:
        mercados = mercados or ("double_chance",) * patas
        return dp.Combinada(
            patas=tuple(cand(i, "1.50", "0.70", market_key=m) for i, m in enumerate(mercados)),
            probabilidad=D(prob), cuota_total=D(cuota), cuota_winamax=D(cuota), mismo_partido=mismo,
        )

    def test_perfil_doble_oportunidad_y_como_mucho_dos_de_goles(self) -> None:
        assert dp.encaja_con_perfil(self._combo("0.3", "4", mercados=("double_chance", "totals", "btts")))
        assert not dp.encaja_con_perfil(self._combo("0.3", "4", mercados=("h2h", "totals", "btts")))
        assert not dp.encaja_con_perfil(
            self._combo("0.3", "4", mercados=("double_chance", "totals", "btts", "team_home_totals_goals")),
        )

    def test_si_ninguna_encaja_gana_la_que_lleva_doble_oportunidad(self) -> None:
        # Doble + 3 de goles no encaja del todo, pero se parece más que 4 de goles.
        con_doble = self._combo(
            "0.15", "4.00", mercados=("double_chance", "totals", "btts", "team_home_totals_goals"),
        )
        solo_goles = self._combo(
            "0.20", "4.00", mercados=("totals", "btts", "team_home_totals_goals", "totals"),
        )
        assert dp.puntuar_combinada(con_doble) > dp.puntuar_combinada(solo_goles)

    def test_el_perfil_va_antes_que_el_valor(self) -> None:
        perfil = self._combo("0.24", "4.00", mercados=("double_chance", "h2h", "totals"))  # EV −0,04
        sin_perfil = self._combo("0.27", "4.00", mercados=("h2h", "h2h", "totals"))  # EV +0,08
        assert dp.puntuar_combinada(perfil) > dp.puntuar_combinada(sin_perfil)

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
            {"market_key": "match_highest_scoring_half", "outcome_name": "2ª mitad"},
            {"recommendable": False},
            {"commence_time": SAQUE + timedelta(hours=3)},
        ],
    )
    def test_quedan_fuera(self, cambio: dict) -> None:
        assert self._ids([linea(**cambio)]) == []

    @pytest.mark.parametrize(
        ("market_key", "outcome"),
        [
            ("totals", "Under"),
            ("totals", "Menos de 2.5"),
            ("team_home_totals_goals", "Under"),
            ("team_away_totals_goals", "menos"),
            ("btts", "No"),
        ],
    )
    def test_menos_goles_nunca_es_pick(self, market_key: str, outcome: str) -> None:
        ln = linea(market_key=market_key, outcome_name=outcome, outcome_point=D("2.5"))
        assert self._ids([ln]) == []

    @pytest.mark.parametrize(
        "market_key", ["totals", "team_home_totals_goals", "team_away_totals_goals"],
    )
    def test_mas_goles_sigue_entrando(self, market_key: str) -> None:
        ln = linea(market_key=market_key, outcome_name="Over", outcome_point=D("1.5"))
        assert self._ids([ln]) == [1]

    def test_ambos_marcan_si_sigue_entrando(self) -> None:
        assert self._ids([linea(market_key="btts", outcome_name="Yes")]) == [1]


class TestCandidatasModeloV2:
    """Reglas del modelo v2: UEFA entra en fase de liga, no en eliminatorias (prórroga)."""

    REGLAS = ReglasModelo(model_version="dc-test", familias=None, min_partidos=5, exigir_mercado=True)
    FEB = datetime(2027, 2, 17, 20, 0, tzinfo=timezone.utc)

    def _ids(self, lineas, saque: datetime = SAQUE) -> list[int]:
        return [
            c.match_id
            for c in candidatas_desde_lineas(
                lineas, desde=saque - timedelta(hours=2), hasta=saque + timedelta(hours=2),
                reglas=self.REGLAS,
            )
        ]

    def _linea_v2(self, **kw) -> GradedOddsLine:
        return linea(**{"model_version": "dc-test", "con_mercado": True, "partidos_min": 9, **kw})

    def test_champions_en_fase_de_liga_entra(self) -> None:
        assert self._ids([self._linea_v2(league_sport_key="soccer_uefa_champs_league")]) == [1]

    def test_eliminatoria_uefa_queda_fuera_por_la_prorroga(self) -> None:
        ln = self._linea_v2(league_sport_key="soccer_uefa_champs_league", commence_time=self.FEB)
        assert self._ids([ln], saque=self.FEB) == []

    def test_liga_domestica_en_febrero_entra(self) -> None:
        assert self._ids([self._linea_v2(commence_time=self.FEB)], saque=self.FEB) == [1]

    def test_equipo_uefa_con_pocos_partidos_queda_fuera(self) -> None:
        ln = self._linea_v2(league_sport_key="soccer_uefa_champs_league", partidos_min=2)
        assert self._ids([ln]) == []


class TestConsultarPicksDeHoy:
    """``GET /today`` responde ``sin_partidos`` él solo: el POST tiene cupo (1 cada 3 min)."""

    def _consultar(self, monkeypatch, *, fila=None, partidos=()):
        monkeypatch.setattr(fdp, "fila_del_dia", lambda _s, _dia: fila)
        monkeypatch.setattr(fdp, "partidos_del_dia", lambda _s, _desde, _hasta: list(partidos))
        return fdp.consultar_picks_de_hoy(None, SAQUE)  # type: ignore[arg-type]

    def test_con_fila_guardada_esta_listo(self, monkeypatch) -> None:
        fila = object()
        res = self._consultar(monkeypatch, fila=fila, partidos=[1])
        assert res is not None and res.estado == "listo" and res.fila is fila

    def test_sin_partidos_no_queda_pendiente(self, monkeypatch) -> None:
        res = self._consultar(monkeypatch)
        assert res is not None and res.estado == "sin_partidos"
        assert res.motivo == fdp.MOTIVO_SIN_PARTIDOS

    def test_con_partidos_y_sin_fila_queda_pendiente(self, monkeypatch) -> None:
        assert self._consultar(monkeypatch, partidos=[1, 2]) is None
