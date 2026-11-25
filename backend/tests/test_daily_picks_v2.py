"""Tests de la selección de picks del día v2 (``daily_picks_v2``, reglas v5) y de su ventana,
sin BD."""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
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
#: Bandas de las reglas v5.
SIMPLES_MIN, SIMPLES_MAX = D("1.30"), D("2.00")


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


#: Un mercado de cada grupo para las pruebas que necesitan variedad (como mucho 2 por grupo).
MERCADOS_VARIADOS: tuple[tuple[str, str, str | None], ...] = (
    ("double_chance", "1X", None),
    ("h2h", "Local", None),
    ("totals", "Over", "2.5"),
    ("match_highest_scoring_half", "2ª mitad", None),
    ("btts", "Yes", None),
)


def variada(match_id: int, odds: str, p: str) -> dp.Candidata:
    """Como :func:`cand`, con el mercado según el partido (doble, 1X2, goles, mitad, goles…)."""
    mk, outcome, point = MERCADOS_VARIADOS[(match_id - 1) % len(MERCADOS_VARIADOS)]
    return cand(match_id, odds, p, market_key=mk, outcome=outcome, point=point)


def prop(
    match_id: int, odds: str, p: str, *, jugador: str = "Lewandowski",
    market_key: str = "player_sot_alt", vector: tuple[float, ...] | None = (0.6,) * 11,
) -> dp.Candidata:
    """Prop de jugador "más de" con su P condicionada a los goles del local (B14d)."""
    return dp.Candidata(
        match_id=match_id, home_team=f"Local {match_id}", away_team=f"Visitante {match_id}",
        league="La Liga", commence_time=SAQUE, market_key=market_key,
        outcome_name=f"{jugador} over 0.5", outcome_point=D("0.5"), offered_odds=D(odds),
        p_model=D(p), lambda_home=D("1.8"), lambda_away=D("1.2"), player_name=jugador,
        prop_condicional=("home", vector) if vector is not None else None,
    )


def simples(cands, **kw):
    """Simples sin el tope por grupo de mercado (las ``cand`` son todas doble oportunidad)."""
    kw.setdefault("max_por_grupo", None)
    return dp.elegir_simples(cands, odds_min=SIMPLES_MIN, odds_max=SIMPLES_MAX, **kw)


def max_por_grupo(combo: dp.Combinada) -> int:
    return max(Counter(dp.grupo_de_mercado(p.market_key) for p in combo.patas).values())


def por_ev(combo: dp.Combinada) -> tuple:
    """Puntuación de prueba (no es la regla de producción): EV y después menos patas."""
    return (combo.ev, -len(combo.patas))


class TestElegirSimples:
    def test_solo_cuotas_dentro_de_la_banda(self) -> None:
        picks, _ = simples([cand(1, "1.29", "0.90"), cand(2, "2.01", "0.70"), cand(3, "1.50", "0.70")])
        assert [p.candidata.match_id for p in picks] == [3]

    def test_la_banda_de_las_simples_llega_a_2(self) -> None:
        # Reglas v5: 1,30–2,00 (antes 1,70) por defecto en la configuración.
        assert Settings.model_fields["picks_odds_min"].default == 1.30
        assert Settings.model_fields["picks_odds_max"].default == 2.00
        picks, _ = simples([cand(1, "1.95", "0.56"), cand(2, "2.00", "0.55"), cand(3, "2.05", "0.60")])
        assert [p.candidata.match_id for p in picks] == [1, 2]

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
        assert meta["motivo_simples"] == "Solo 1 partido con cuota entre 1,30 y 2,00"

    def test_la_mitad_con_mas_goles_puede_ser_simple(self) -> None:
        mitad = cand(1, "1.95", "0.56", market_key="match_highest_scoring_half", outcome="2ª mitad")
        picks, _ = dp.elegir_simples(
            [mitad, cand(2, "1.40", "0.74")], odds_min=SIMPLES_MIN, odds_max=SIMPLES_MAX,
        )
        assert mitad in [p.candidata for p in picks]
        assert dp.grupo_de_mercado(mitad.market_key) == "mitad"

    def test_un_prop_entra_como_simple_marcado_como_experimental(self) -> None:
        tiros = prop(1, "1.45", "0.74")
        picks, _ = dp.elegir_simples(
            [tiros, cand(1, "1.40", "0.70"), cand(2, "1.50", "0.68")],
            odds_min=SIMPLES_MIN, odds_max=SIMPLES_MAX,
        )
        assert picks[0].candidata == tiros
        d = dp.pata_a_dict(tiros, model_version="dc-test", odds_captured_at=SAQUE, sin_valor=False)
        assert d["experimental"] is True
        assert d["player_name"] == "Lewandowski"
        assert d["market_key"] == "player_sot_alt" and d["outcome_point"] == "0.5"

    def test_props_cuentan_en_el_tope_por_grupo(self) -> None:
        # Tres tiros a puerta con más P que nada: solo entran dos; el partido del tercero (el de
        # menos P) da su doble oportunidad.
        cands = [prop(i, "1.40", f"0.8{i}", jugador=f"Delantero {i}") for i in (1, 2, 3)]
        cands.append(cand(1, "1.35", "0.72"))
        picks, _ = dp.elegir_simples(cands, odds_min=SIMPLES_MIN, odds_max=SIMPLES_MAX)
        grupos = Counter(dp.grupo_de_mercado(p.candidata.market_key) for p in picks)
        assert grupos == {"tiros": 2, "doble": 1}

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
        picks, _ = dp.elegir_simples(cands, odds_min=SIMPLES_MIN, odds_max=SIMPLES_MAX)
        assert len(picks) == dp.MAX_SIMPLES_POR_GRUPO

    def test_desempate_determinista(self) -> None:
        a, b = cand(7, "1.50", "0.70"), cand(3, "1.50", "0.70")
        assert [p.candidata.match_id for p in simples([a, b])[0]] == [3, 7]
        assert [p.candidata.match_id for p in simples([b, a])[0]] == [3, 7]


class TestCombinada:
    def test_partidos_distintos_y_cuota_total_en_banda(self) -> None:
        cands = [variada(i, "1.45", "0.70") for i in range(1, 6)]
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

    def test_patas_fuera_de_1_18_a_1_80_no_entran(self) -> None:
        grupos = dp.grupos_para_combinada([cand(1, "1.81", "0.55"), cand(2, "1.17", "0.85")])
        assert grupos == []

    def test_las_patas_llegan_a_1_80(self) -> None:
        # Reglas v5: patas de 1,18 a 1,80 (antes 1,60); tramo corto hasta 1,45.
        grupos = dp.grupos_para_combinada([cand(1, "1.80", "0.55"), cand(2, "1.18", "0.84")])
        assert sorted(str(c.offered_odds) for g in grupos for c in g) == ["1.18", "1.80"]
        assert dp._tramo_pata(cand(1, "1.44", "0.7")) == 0  # noqa: SLF001
        assert dp._tramo_pata(cand(1, "1.45", "0.7")) == 1  # noqa: SLF001

    def test_tres_patas_largas_llegan_a_la_banda(self) -> None:
        # 1,60³ ≈ 4,10: con el tope de 1,60 no se llegaba con 3 patas.
        cands = [variada(i, "1.60", "0.64") for i in range(1, 4)]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"))
        assert combo is not None and len(combo.patas) == 3

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

    def test_por_partido_como_mucho_tres_patas_por_tramo(self) -> None:
        # Seis grupos en cada tramo: entran las tres de más P de grupos distintos (≤ 6 por partido).
        cortas = [
            cand(1, "1.30", "0.80", market_key="totals", outcome="Over", point="1.5"),
            cand(1, "1.32", "0.79", market_key="btts", outcome="Yes"),  # goles otra vez: fuera
            cand(1, "1.25", "0.78"),
            prop(1, "1.35", "0.77"),
            prop(1, "1.40", "0.76", market_key="player_anytime_scorer"),  # cuarta: fuera
        ]
        largas = [
            cand(1, "1.70", "0.58", market_key="h2h", outcome="Local 1"),
            cand(1, "1.75", "0.55", market_key="match_highest_scoring_half", outcome="2ª mitad"),
            cand(1, "1.60", "0.62", market_key="totals", outcome="Over", point="2.5"),
            prop(1, "1.80", "0.54", market_key="player_anytime_scorer", jugador="Raphinha"),
        ]
        (grupo,) = dp.grupos_para_combinada([*cortas, *largas])
        assert len(grupo) == 2 * dp.COMBO_PATAS_POR_TRAMO
        assert {(c.market_key, str(c.offered_odds)) for c in grupo} == {
            ("totals", "1.30"), ("double_chance", "1.25"), ("player_sot_alt", "1.35"),
            ("totals", "1.60"), ("h2h", "1.70"), ("match_highest_scoring_half", "1.75"),
        }

    def test_con_cinco_patas_cortas_se_llega_a_la_banda(self) -> None:
        cands = [variada(i, "1.35", "0.78") for i in range(1, 6)]  # 1,35⁵ ≈ 4,48
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
            if i <= 2:
                cands += [
                    cand(i, "1.27", "0.76", market_key="totals", outcome="Over", point="1.5"),
                    cand(i, "1.50", "0.62", market_key="totals", outcome="Over", point="2.5"),
                ]
            else:
                cands += [
                    cand(i, "1.27", "0.76"),
                    cand(i, "1.50", "0.62", market_key="h2h", outcome=f"Local {i}"),
                ]
        (grupo, *_) = dp.grupos_para_combinada(cands)
        assert [str(c.offered_odds) for c in grupo] == ["1.27", "1.50"]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"), puntuar=por_ev)
        assert combo is not None
        assert not combo.mismo_partido

    def test_ninguna_combinada_con_tres_patas_del_mismo_grupo(self) -> None:
        # Sin el tope ganarían cuatro patas de goles (más P); con él, como mucho dos.
        cands = []
        for i in range(1, 6):
            cands += [
                cand(i, "1.45", "0.74", market_key="totals", outcome="Over", point="1.5"),
                cand(i, "1.45", "0.70"),
                cand(i, "1.50", "0.66", market_key="h2h", outcome=f"Local {i}"),
            ]
        grupos = dp.grupos_para_combinada(cands)
        combos = list(dp.enumerar_combinadas(grupos, total_min=D("4"), total_max=D("6")))
        assert combos
        assert all(max_por_grupo(c) <= dp.COMBO_MAX_PATAS_POR_GRUPO for c in combos)
        mejor = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"))
        assert mejor is not None and max_por_grupo(mejor) <= 2
        # Con el tope desactivado sí aparecen (el tope es lo que las quita).
        sin_tope = dp.enumerar_combinadas(grupos, total_min=D("4"), total_max=D("6"), max_por_grupo=5)
        assert any(max_por_grupo(c) >= 3 for c in sin_tope)

    def test_tope_por_grupo_tambien_con_selecciones_del_mismo_partido(self) -> None:
        combos = list(dp.enumerar_combinadas_mismo_partido(
            [
                cand(1, "1.30", "0.80", market_key="totals", outcome="Over", point="1.5"),
                cand(1, "1.45", "0.66", market_key="btts", outcome="Yes"),
                cand(2, "1.40", "0.70", market_key="team_home_totals_goals", outcome="Over", point="0.5"),
                cand(3, "1.60", "0.60", market_key="totals", outcome="Over", point="2.5"),
                cand(3, "1.30", "0.74"),
            ],
            total_min=D("2"), total_max=D("10"),
        ))
        assert combos
        assert all(max_por_grupo(c) <= dp.COMBO_MAX_PATAS_POR_GRUPO for c in combos)

    def test_un_prop_puede_ser_pata_de_otro_partido(self) -> None:
        cands = [prop(1, "1.60", "0.66"), variada(2, "1.60", "0.64"), variada(3, "1.60", "0.64")]
        combo = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"))
        assert combo is not None and not combo.mismo_partido
        assert any(dp.es_prop(p) for p in combo.patas)
        d = dp.combinada_a_dict(combo, model_version="dc-test", odds_captured_at=SAQUE)
        assert d["experimental"] is True
        assert [lg["experimental"] for lg in d["legs"]].count(True) == 1

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
        mitad = clasico("match_highest_scoring_half", "2ª mitad", "1.75", "0.5")
        assert dp.mascara_de_pata(mitad) is None
        assert dp.pesos_de_pata(mitad) is None
        combos = list(dp.enumerar_combinadas_mismo_partido(
            [self.GANA, mitad], total_min=D("1"), total_max=D("10"), min_patas=2,
        ))
        assert combos == []

    def _prop_clasico(self, vector: tuple[float, ...] | None) -> dp.Candidata:
        return dp.Candidata(**{
            **prop(9, "1.50", "0.66", vector=vector).__dict__,
            "home_team": "FC Barcelona", "away_team": "Real Madrid",
        })

    @pytest.mark.parametrize("vector", [None, (0.6,) * 3], ids=["sin_vector", "vector_corto"])
    def test_un_prop_sin_pesos_no_se_junta_en_el_mismo_partido(self, vector) -> None:
        tiros = self._prop_clasico(vector)
        assert dp.pesos_de_pata(tiros) is None
        combos = list(dp.enumerar_combinadas_mismo_partido(
            [self.GANA, tiros], total_min=D("1"), total_max=D("10"), min_patas=2,
        ))
        assert combos == []

    def test_un_prop_con_pesos_si_se_junta_en_el_mismo_partido(self) -> None:
        # Con su P condicionada al marcador la matriz sabe valorarlo (como en la del partido).
        tiros = self._prop_clasico(tuple(min(0.3 + 0.15 * k, 0.95) for k in range(11)))
        (combo,) = dp.enumerar_combinadas_mismo_partido(
            [self.GANA, tiros], total_min=D("1"), total_max=D("10"), min_patas=2,
        )
        assert combo.mismo_partido
        assert set(combo.patas) == {self.GANA, tiros}

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

    def test_casi_empatadas_gana_la_mas_variada(self) -> None:
        # 25,1 % y 24,9 % son el mismo 25 % redondeado: decide la variedad (3 grupos frente a 2).
        poco_variada = self._combo("0.2510", "4.20", mercados=("totals", "btts", "double_chance"))
        variada_ = self._combo("0.2490", "4.20", mercados=("totals", "double_chance", "h2h"))
        assert dp.grupos_distintos(poco_variada) == 2 and dp.grupos_distintos(variada_) == 3
        assert dp.puntuar_combinada(variada_) > dp.puntuar_combinada(poco_variada)

    def test_con_mas_de_un_punto_de_diferencia_manda_la_probabilidad(self) -> None:
        poco_variada = self._combo("0.2700", "4.20", mercados=("totals", "btts", "double_chance"))
        variada_ = self._combo("0.2500", "4.20", mercados=("totals", "double_chance", "h2h"))
        assert dp.puntuar_combinada(poco_variada) > dp.puntuar_combinada(variada_)

    def test_el_valor_va_antes_que_la_variedad(self) -> None:
        con_valor = self._combo("0.2500", "4.20", mercados=("totals", "btts", "double_chance"))
        sin_valor = self._combo("0.2500", "3.90", mercados=("totals", "double_chance", "h2h"))
        assert dp.puntuar_combinada(con_valor) > dp.puntuar_combinada(sin_valor)

    def test_ya_no_hace_falta_doble_oportunidad(self) -> None:
        # El perfil del 05/11 ya no existe: sin doble oportunidad puede ganar si tiene más P.
        sin_doble = self._combo("0.30", "4.00", mercados=("h2h", "totals", "match_highest_scoring_half"))
        con_doble = self._combo("0.26", "4.00", mercados=("double_chance", "totals", "h2h"))
        assert dp.puntuar_combinada(sin_doble) > dp.puntuar_combinada(con_doble)

    def test_desempate_determinista(self) -> None:
        cands = [variada(i, "1.45", "0.70") for i in range(1, 7)]
        a = dp.elegir_combinada(cands, total_min=D("4"), total_max=D("6"))
        b = dp.elegir_combinada(list(reversed(cands)), total_min=D("4"), total_max=D("6"))
        assert a is not None and b is not None
        assert {dp.clave_seleccion(p) for p in a.patas} == {dp.clave_seleccion(p) for p in b.patas}

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
        assert d["experimental"] is False
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

    @pytest.mark.parametrize("outcome", ["1ª mitad", "2ª mitad", "Igual"])
    def test_la_mitad_con_mas_goles_vuelve_a_entrar(self, outcome: str) -> None:
        # Fuera desde el 05/11; vuelve con las reglas v5 (2026-11-17).
        assert self._ids([linea(market_key="match_highest_scoring_half", outcome_name=outcome)]) == [1]


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


class _ModeloProps:
    """Lo mínimo de ``ModeloActivo`` que usan las reglas de los picks."""

    tag = "dc-test"
    familias = frozenset({"1x2", "totals", "team_totals", "highest_half"})
    peso_mercado = 0.6
    fit = SimpleNamespace(n_matches=500)
    familias_props = frozenset({"player_sot", "goleador"})


class TestGenerarPicksV5:
    """``generar_picks_v2`` con las reglas v5, sin BD: los props compiten en las simples."""

    AHORA = SAQUE - timedelta(hours=6)
    CFG = SimpleNamespace(
        picks_kickoff_margin_minutes=30, picks_odds_min=1.30, picks_odds_max=2.00,
        picks_combo_odds_min=4.0, picks_combo_odds_max=6.0,
    )

    def _linea_v2(self, match_id: int, **kw) -> GradedOddsLine:
        return linea(**{
            "match_id": match_id, "model_version": "dc-test", "con_mercado": True, "partidos_min": 9,
            "lambda_home": D("1.6"), "lambda_away": D("1.1"), **kw,
        })

    def _generar(self, monkeypatch, lineas: list[GradedOddsLine]):
        monkeypatch.setattr(fdp, "list_graded_winamax_odds", lambda *_a, **_k: lineas)
        monkeypatch.setattr(fdp, "modelo_activo", lambda *_a, **_k: _ModeloProps())
        monkeypatch.setattr(fdp, "ultima_toma_por_partido", lambda *_a, **_k: {})
        monkeypatch.setattr(fdp, "_refresh_one_row", lambda *_a, **_k: None)
        sesion = SimpleNamespace(add=lambda _fila: None, flush=lambda: None)
        return fdp.generar_picks_v2(sesion, self.AHORA, settings=self.CFG)  # type: ignore[arg-type]

    def test_un_prop_sale_como_simple_experimental(self, monkeypatch) -> None:
        lineas = [
            self._linea_v2(
                1, market_key="player_sot_alt", outcome_name="Lewandowski over 0.5",
                outcome_point=D("0.5"), player_name="Lewandowski", offered_odds=D("1.45"),
                model_probability=D("0.78"), con_mercado=False, prop_condicional=("home", (0.7,) * 11),
            ),
            self._linea_v2(2, offered_odds=D("1.40"), model_probability=D("0.74")),
            self._linea_v2(
                3, market_key="match_highest_scoring_half", outcome_name="2ª mitad",
                offered_odds=D("1.95"), model_probability=D("0.53"),
            ),
            self._linea_v2(4, market_key="totals", outcome_name="Under", outcome_point=D("2.5"),
                           offered_odds=D("1.50"), model_probability=D("0.80")),
        ]
        fila = self._generar(monkeypatch, lineas)
        assert fila.rules_version == "v5"
        por_partido = {lg["match_id"]: lg for lg in fila.picks_json}
        assert por_partido[1]["experimental"] is True
        assert por_partido[1]["player_name"] == "Lewandowski"
        assert por_partido[2]["experimental"] is False
        assert por_partido[3]["market_key"] == "match_highest_scoring_half"
        # Menos goles sigue fuera aunque tenga más P que todo.
        assert 4 not in por_partido
        assert fila.selection_meta["candidatas_props"] == 1


class TestMetricasSimplesConProps:
    def test_separa_las_simples_de_props(self) -> None:
        m = fdp.metricas_simples_por_props([
            {"result": "hit", "experimental": True},
            {"result": "miss", "experimental": True},
            {"result": "hit"},
            {"result": "pending", "experimental": False},
        ])
        assert m["simples_con_props"]["hits"] == 1 and m["simples_con_props"]["misses"] == 1
        assert m["simples_con_props"]["rate_pct"] == 50.0
        assert m["simples_sin_props"]["hits"] == 1 and m["simples_sin_props"]["pending"] == 1


class TestRendimiento:
    """El peor caso de la combinada del día sigue acotado (≤ 6 patas por partido, ≤ 8 partidos)."""

    def _muchas(self, n_partidos: int) -> list[dp.Candidata]:
        rnd = random.Random(7)
        mercados = [
            ("double_chance", "1X", None), ("h2h", "Local", None), ("totals", "Over", "2.5"),
            ("match_highest_scoring_half", "2ª mitad", None),
        ]
        out: list[dp.Candidata] = []
        for i in range(1, n_partidos + 1):
            for tramo in (("1.18", "1.44"), ("1.45", "1.80")):
                for mk, outcome, point in mercados:
                    odds = D(str(round(rnd.uniform(float(tramo[0]), float(tramo[1])), 2)))
                    out.append(cand(i, str(odds), str(round(0.95 / float(odds), 3)), market_key=mk,
                                    outcome=outcome, point=point))
                for familia in ("player_sot_alt", "player_anytime_scorer"):
                    odds = D(str(round(rnd.uniform(float(tramo[0]), float(tramo[1])), 2)))
                    out.append(prop(i, str(odds), str(round(0.95 / float(odds), 3)), market_key=familia,
                                    jugador=f"Jugador {i}{familia}{tramo[0]}"))
        return out

    def test_patas_por_partido_acotadas(self) -> None:
        grupos = dp.grupos_para_combinada(self._muchas(12))
        assert len(grupos) == dp.COMBO_MAX_PARTIDOS
        assert all(len(g) <= 2 * dp.COMBO_PATAS_POR_TRAMO for g in grupos)
        for g in grupos:
            for tramo in (0, 1):
                del_tramo = [dp.grupo_de_mercado(c.market_key) for c in g if dp._tramo_pata(c) == tramo]  # noqa: SLF001
                assert len(del_tramo) == len(set(del_tramo))

    def test_la_combinada_respeta_el_tope_por_grupo(self) -> None:
        combo = dp.elegir_combinada(self._muchas(12), total_min=D("4"), total_max=D("6"))
        assert combo is not None
        assert max_por_grupo(combo) <= dp.COMBO_MAX_PATAS_POR_GRUPO


class TestConsultarPicksDeHoy:
    """``GET /today`` responde ``sin_partidos`` él solo: el POST tiene cupo (1 cada 3 min)."""

    def _consultar(self, monkeypatch, *, fila=None, partidos=(), proximo=None, pedidos=None):
        monkeypatch.setattr(fdp, "fila_del_dia", lambda _s, _dia: fila)
        monkeypatch.setattr(fdp, "partidos_del_dia", lambda _s, _desde, _hasta: list(partidos))

        def proximo_partido(_s, desde):
            if pedidos is not None:
                pedidos.append(desde)
            return proximo

        monkeypatch.setattr(fdp, "proximo_partido", proximo_partido)
        return fdp.consultar_picks_de_hoy(None, SAQUE)  # type: ignore[arg-type]

    def test_con_fila_guardada_esta_listo(self, monkeypatch) -> None:
        fila = object()
        res = self._consultar(monkeypatch, fila=fila, partidos=[1])
        assert res is not None and res.estado == "listo" and res.fila is fila

    def test_sin_partidos_no_queda_pendiente(self, monkeypatch) -> None:
        res = self._consultar(monkeypatch)
        assert res is not None and res.estado == "sin_partidos"
        assert res.motivo == fdp.MOTIVO_SIN_PARTIDOS
        assert res.proximo_partido is None

    def test_sin_partidos_dice_el_proximo_partido_desde_manana(self, monkeypatch) -> None:
        viernes = datetime(2026, 11, 20, 19, 30, tzinfo=timezone.utc)
        pedidos: list[datetime] = []
        res = self._consultar(monkeypatch, proximo=viernes, pedidos=pedidos)
        assert res is not None and res.proximo_partido == viernes
        # Se busca desde la medianoche de Madrid: los de hoy ya no pueden ser pick.
        assert pedidos == [fdp.ventana_del_dia(SAQUE, 0)[1]]

    def test_con_partidos_y_sin_fila_queda_pendiente(self, monkeypatch) -> None:
        assert self._consultar(monkeypatch, partidos=[1, 2]) is None
