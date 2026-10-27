"""Probabilidad del modelo v2 para cada línea de Winamax (fase 3, bloque B5). Puro.

Parte de la predicción de un partido (:class:`MatchPrediction`: λ local y visitante, ρ de
Dixon–Coles y cuota de goles de la 2ª parte) y de la matriz de marcadores que sale de ella, con
la corrección de Dixon–Coles aplicada a **todos** los mercados (el modelo actual la usa en el
1X2 pero no en los totales). Si se le pasa una calibración (``model_calibration_v2``), la aplica
al suceso canónico de cada familia y deriva el resto (1X2 renormalizado, "menos de" = 1 − "más
de" en líneas .5…), para que las probabilidades sigan siendo coherentes entre sí.

Mercados: 1X2, doble oportunidad, más/menos de goles del partido y de cada equipo, ambos marcan
y mitad con más goles. El resto devuelve ``None`` (y la línea sigue con el modelo actual).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property

import numpy as np

from backend.services import poisson_model as pm
from backend.services.model_calibration_v2 import Calibracion
from backend.services.winamax_normalization import (
    highest_scoring_half_side,
    normalize_double_chance_outcome,
    normalize_h2h_outcome,
)

TAMANO_REJILLA = 11
_EMPATE = frozenset({"draw", "empate", "x", "tie"})
MERCADOS_V2: frozenset[str] = frozenset({
    "h2h",
    "double_chance",
    "totals",
    "btts",
    "team_home_totals_goals",
    "team_away_totals_goals",
    "match_highest_scoring_half",
})


@dataclass(frozen=True)
class MatchPrediction:
    lambda_home: float
    lambda_away: float
    rho: float = 0.0
    second_half_share: float = pm.SECOND_HALF_GOAL_SHARE

    @cached_property
    def rejilla(self) -> np.ndarray:
        """P(local = i, visitante = j) con Dixon–Coles, normalizada a 1."""
        g = np.array(pm._build_grid(  # noqa: SLF001
            self.lambda_home, self.lambda_away, size=TAMANO_REJILLA, dixon_coles_rho=self.rho,
        ))
        return g / g.sum()

    @cached_property
    def p_1x2(self) -> tuple[float, float, float]:
        g = self.rejilla
        i, j = np.indices(g.shape)
        return float(g[i > j].sum()), float(g[i == j].sum()), float(g[i < j].sum())

    @cached_property
    def p_mitades(self) -> tuple[float, float, float]:
        return pm.probabilities_highest_scoring_half(
            self.lambda_home + self.lambda_away, second_half_share=self.second_half_share,
        )


def _lado_mas_menos(outcome: str) -> str | None:
    n = (outcome or "").strip().lower()
    if n in {"over", "más", "mas"} or n.startswith(("over", "más", "mas ")):
        return "over"
    if n in {"under", "menos"} or n.startswith(("under", "menos")):
        return "under"
    return None


def _mas_menos(goles: np.ndarray, pesos: np.ndarray, point: float) -> tuple[float, float]:
    """``(P(más de point), P(menos de point))`` sobre una variable discreta (push excluido)."""
    return float(pesos[goles > point].sum()), float(pesos[goles < point].sum())


def _lados_1x2(outcome: str, home: str, away: str, *, doble: bool = False) -> set[str] | None:
    """Resultados (``H``/``D``/``A``) que hacen ganar una selección de 1X2 (o de doble
    oportunidad con ``doble=True``)."""
    if doble:
        canon = normalize_double_chance_outcome(outcome, home_team=home, away_team=away)
        partes = [p.strip() for p in canon.split(" or ")]
    else:
        partes = [normalize_h2h_outcome(outcome, home_team=home, away_team=away)]
    lados: set[str] = set()
    for p in partes:
        if p.lower() in _EMPATE:
            lados.add("D")
        elif p == home.strip():
            lados.add("H")
        elif p == away.strip():
            lados.add("A")
        else:
            return None
    return lados or None


def probability_for_line(
    pred: MatchPrediction,
    market_key: str,
    outcome_name: str,
    outcome_point: float | None,
    home_team: str,
    away_team: str,
    calibracion: Calibracion | None = None,
) -> float | None:
    """P de que gane la selección (``None`` si el mercado no lo cubre el v2)."""
    cal = calibracion or Calibracion()
    mk = market_key

    if mk in {"h2h", "double_chance"}:
        lados = _lados_1x2(outcome_name, home_team, away_team, doble=mk == "double_chance")
        if lados is None or (mk == "h2h" and len(lados) != 1) or (mk == "double_chance" and len(lados) != 2):
            return None
        ph, pd, pa = cal.tres_vias("1x2", pred.p_1x2)
        return sum({"H": ph, "D": pd, "A": pa}[s] for s in lados)

    if mk == "match_highest_scoring_half":
        mitad = highest_scoring_half_side(outcome_name)
        if mitad is None:
            return None
        p1, pig, p2 = cal.tres_vias("highest_half", pred.p_mitades)
        return {"1h": p1, "tie": pig, "2h": p2}[mitad]

    g = pred.rejilla
    i, j = np.indices(g.shape)

    if mk == "btts":
        n = (outcome_name or "").strip().lower()
        p_si = cal.aplicar("btts", float(g[(i >= 1) & (j >= 1)].sum()))
        if n in {"yes", "sí", "si"}:
            return p_si
        if n == "no":
            return 1.0 - p_si
        return None

    if mk in {"totals", "team_home_totals_goals", "team_away_totals_goals"}:
        lado = _lado_mas_menos(outcome_name)
        if lado is None or outcome_point is None:
            return None
        point = float(outcome_point)
        goles = i + j if mk == "totals" else (i if mk == "team_home_totals_goals" else j)
        familia = "totals" if mk == "totals" else "team_totals"
        p_mas, p_menos = _mas_menos(goles.ravel(), g.ravel(), point)
        p_mas_cal = cal.aplicar(familia, p_mas)
        if math.isclose(point % 1, 0.5):
            return p_mas_cal if lado == "over" else 1.0 - p_mas_cal
        # Línea entera: el push no se calibra; "menos de" escala con lo que deja "más de".
        if lado == "over":
            return p_mas_cal
        resto = 1.0 - p_mas
        return p_menos * (1.0 - p_mas_cal) / resto if resto > 0 else 0.0

    return None


# ---------------------------------------------------------------------------
# Probabilidad del mercado (cuotas de Winamax sin margen)
# ---------------------------------------------------------------------------


def sin_margen_power(cuotas: list[float]) -> list[float]:
    """Probabilidades sin margen por el método "power": ``Σ (1/cuota)^k = 1``.

    Quita más margen a los resultados poco probables que el proporcional (sesgo
    favorito–outsider). Si la suma de 1/cuota ya es ≤ 1, se normaliza sin más.
    """
    q = [1.0 / c for c in cuotas]
    s = sum(q)
    if s <= 1.0:
        return [x / s for x in q]
    lo, hi = 1.0, 10.0
    for _ in range(80):
        k = (lo + hi) / 2
        if sum(x ** k for x in q) > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return [x ** k for x in q]


ClaveLinea = tuple[str, str, float | None]


def probabilidades_sin_margen(
    lineas: list[tuple[str, str, float | None, float]], home_team: str, away_team: str,
) -> dict[ClaveLinea, float]:
    """P sin margen de cada línea de un partido: ``(mercado, selección, línea) → P``.

    ``lineas`` = ``(market_key, outcome_name, outcome_point, cuota)``. Solo se calculan los
    mercados completos: 1X2 (y con él la doble oportunidad), más/menos de goles del partido y de
    cada equipo, ambos marcan y mitad con más goles.
    """
    grupos: dict[tuple[str, float | None], dict[str, float]] = {}
    for mk, oc, pt, cuota in lineas:
        if mk in MERCADOS_V2 and cuota > 1.0:
            grupos.setdefault((mk, pt), {})[oc] = cuota
    out: dict[ClaveLinea, float] = {}

    h2h = grupos.get(("h2h", None), {})
    lados_h2h: dict[str, tuple[str, float]] = {}
    for oc, cuota in h2h.items():
        lados = _lados_1x2(oc, home_team, away_team)
        if lados and len(lados) == 1:
            lados_h2h[next(iter(lados))] = (oc, cuota)
    if set(lados_h2h) == {"H", "D", "A"}:
        orden = ("H", "D", "A")
        ps = dict(zip(orden, sin_margen_power([lados_h2h[s][1] for s in orden])))
        for s in orden:
            out[("h2h", lados_h2h[s][0], None)] = ps[s]
        for oc in grupos.get(("double_chance", None), {}):
            lados = _lados_1x2(oc, home_team, away_team, doble=True)
            if lados and len(lados) == 2:
                out[("double_chance", oc, None)] = sum(ps[s] for s in lados)

    for (mk, pt), sel in grupos.items():
        if mk in {"totals", "team_home_totals_goals", "team_away_totals_goals"} and pt is not None:
            mas = [oc for oc in sel if _lado_mas_menos(oc) == "over"]
            menos = [oc for oc in sel if _lado_mas_menos(oc) == "under"]
            if len(mas) == 1 and len(menos) == 1 and math.isclose(pt % 1, 0.5):
                p_mas, p_menos = sin_margen_power([sel[mas[0]], sel[menos[0]]])
                out[(mk, mas[0], pt)] = p_mas
                out[(mk, menos[0], pt)] = p_menos
        elif mk == "btts":
            si = [oc for oc in sel if oc.strip().lower() in {"yes", "sí", "si"}]
            no = [oc for oc in sel if oc.strip().lower() == "no"]
            if len(si) == 1 and len(no) == 1:
                p_si, p_no = sin_margen_power([sel[si[0]], sel[no[0]]])
                out[(mk, si[0], None)], out[(mk, no[0], None)] = p_si, p_no
        elif mk == "match_highest_scoring_half":
            por_mitad = {highest_scoring_half_side(oc): oc for oc in sel}
            if set(por_mitad) == {"1h", "tie", "2h"}:
                orden = [por_mitad[k] for k in ("1h", "tie", "2h")]
                for oc, p in zip(orden, sin_margen_power([sel[oc] for oc in orden])):
                    out[(mk, oc, None)] = p
    return out


def mezclar(p_modelo: float, p_mercado: float | None, peso_mercado: float) -> float:
    """Mezcla lineal ``(1 − w)·modelo + w·mercado`` (sin mercado, el modelo tal cual)."""
    if p_mercado is None or peso_mercado <= 0:
        return p_modelo
    return (1.0 - peso_mercado) * p_modelo + peso_mercado * p_mercado


__all__ = [
    "MERCADOS_V2",
    "MatchPrediction",
    "mezclar",
    "probabilidades_sin_margen",
    "probability_for_line",
    "sin_margen_power",
]
