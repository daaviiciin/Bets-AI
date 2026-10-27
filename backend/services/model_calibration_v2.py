"""Calibración del modelo v2 por familia de mercado y métricas de evaluación (fase 3, B7). Puro.

Platt acotado: ``p_cal = σ(a + b·logit p)`` con ``b ∈ [0,5; 1]``. Con ``b ≤ 1`` la calibración
solo puede **bajar** la confianza del modelo (acercar sus probabilidades al centro), nunca
exagerarla: es la protección principal contra el EV inflado. Se ajusta con predicciones fuera de
muestra del backtest y solo si hay al menos :data:`MIN_EVENTOS` sucesos de esa familia; si no, la
familia queda sin calibrar (identidad).

Familias: ``1x2`` (también da la doble oportunidad), ``totals``, ``team_totals``, ``btts`` y
``highest_half``. En las de tres resultados se calibra cada uno y se renormaliza.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize

MIN_EVENTOS = 150
B_MIN, B_MAX = 0.5, 1.0
A_MAX = 1.0
_EPS = 1e-6

FAMILIA_DE_MERCADO: dict[str, str] = {
    "h2h": "1x2",
    "double_chance": "1x2",
    "totals": "totals",
    "team_home_totals_goals": "team_totals",
    "team_away_totals_goals": "team_totals",
    "btts": "btts",
    "match_highest_scoring_half": "highest_half",
}


def familia(market_key: str) -> str | None:
    return FAMILIA_DE_MERCADO.get(market_key)


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def _sigmoide(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


@dataclass(frozen=True)
class Platt:
    a: float = 0.0
    b: float = 1.0
    n: int = 0

    def aplicar(self, p: float) -> float:
        if self.a == 0.0 and self.b == 1.0:
            return p
        return _sigmoide(self.a + self.b * _logit(p))


@dataclass(frozen=True)
class Calibracion:
    """Platt por familia; una familia ausente no se calibra."""

    por_familia: dict[str, Platt] = field(default_factory=dict)

    def aplicar(self, fam: str, p: float) -> float:
        platt = self.por_familia.get(fam)
        return platt.aplicar(p) if platt else p

    def tres_vias(self, fam: str, ps: tuple[float, float, float]) -> tuple[float, float, float]:
        cal = [self.aplicar(fam, p) for p in ps]
        total = sum(cal)
        return tuple(c / total for c in cal) if total > 0 else ps  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        return {k: {"a": v.a, "b": v.b, "n": v.n} for k, v in self.por_familia.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Calibracion:
        return cls({k: Platt(float(v["a"]), float(v["b"]), int(v.get("n", 0))) for k, v in (d or {}).items()})


def ajustar_platt(ps: Sequence[float], ys: Sequence[int], *, min_n: int = MIN_EVENTOS) -> Platt:
    """Platt acotado por máxima verosimilitud; identidad si hay menos de ``min_n`` sucesos."""
    n = len(ps)
    if n < min_n:
        return Platt(n=n)
    z = np.array([_logit(p) for p in ps])
    y = np.array(ys, dtype=float)

    def nll(params: np.ndarray) -> tuple[float, np.ndarray]:
        a, b = params
        q = 1.0 / (1.0 + np.exp(-(a + b * z)))
        q = np.clip(q, _EPS, 1 - _EPS)
        val = -float(np.sum(y * np.log(q) + (1 - y) * np.log(1 - q)))
        r = q - y
        return val, np.array([float(r.sum()), float((r * z).sum())])

    res = minimize(
        nll, np.array([0.0, 1.0]), jac=True, method="L-BFGS-B",
        bounds=[(-A_MAX, A_MAX), (B_MIN, B_MAX)],
    )
    return Platt(float(res.x[0]), float(res.x[1]), n)


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------


def log_loss(ps: Sequence[float], ys: Sequence[int]) -> float:
    if not ps:
        return float("nan")
    total = 0.0
    for p, y in zip(ps, ys):
        p = min(max(p, _EPS), 1 - _EPS)
        total -= math.log(p) if y else math.log(1 - p)
    return total / len(ps)


def brier(ps: Sequence[float], ys: Sequence[int]) -> float:
    if not ps:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def ece(ps: Sequence[float], ys: Sequence[int], *, bins: int = 5) -> float:
    """Error de calibración esperado: |P media − frecuencia real| por tramo, ponderado."""
    if not ps:
        return float("nan")
    total = 0.0
    for k in range(bins):
        lo, hi = k / bins, (k + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (k == bins - 1 and p == 1.0)]
        if idx:
            p_media = sum(ps[i] for i in idx) / len(idx)
            frec = sum(ys[i] for i in idx) / len(idx)
            total += len(idx) / len(ps) * abs(p_media - frec)
    return total


__all__ = [
    "Calibracion",
    "FAMILIA_DE_MERCADO",
    "MIN_EVENTOS",
    "Platt",
    "ajustar_platt",
    "brier",
    "ece",
    "familia",
    "log_loss",
]
