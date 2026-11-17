"""Tiros a puerta de equipo y de partido (fase 3, bloque B6). Puro: numpy + scipy.

Mismo ajuste que los goles (``team_ratings_model``: Poisson penalizado con ridge, fuerza del
rival, liga y campo encogidos, decaimiento temporal) pero con los **tiros a puerta** de cada
equipo como respuesta::

    log S_local     = μ + μ_l + campo + campo_l + ataque_i − defensa_j
    log S_visitante = μ + μ_l           + ataque_j − defensa_i

Los tiros a puerta están **sobredispersos** frente a un Poisson (varianza > media), así que la
probabilidad de cada línea sale de una **binomial negativa** con la media del ajuste y un tamaño
``r`` común estimado por momentos con los residuos (``Var = S + S²/r``). Si no hay
sobredispersión, ``r = None`` y queda un Poisson.

Lo usan los mercados ``match_sot_totals``, ``team_home_sot_totals`` y ``team_away_sot_totals``
(``market_probabilities``) y, como medida de exposición, los props de tiros a puerta y paradas
de jugador (``player_props_model``). Se desactiva con ``SotHyper(activo=False)``: entonces
:func:`fit_sot` devuelve ``None`` y esas líneas siguen con el modelo actual.

La mezcla goles–xG del modelo de goles ya está en ``team_ratings_model`` (``xg_weight``); aquí no
se repite.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.stats import nbinom, poisson

from backend.services.model_dataset import TrainingMatch, assert_sin_fuga
from backend.services.team_ratings_model import (
    Parametros,
    _ajustar_poisson,  # noqa: PLC2701 - mismo ajuste, otra respuesta
    pesos_temporales,
)

#: Partidos con tiros a puerta de los dos equipos necesarios para ajustar.
MIN_PARTIDOS_SOT = 50
#: Tamaño mínimo de la binomial negativa (más pequeño = colas exageradas con pocos datos).
R_MIN = 2.0


@dataclass(frozen=True)
class SotHyper:
    #: ``False`` desactiva el modelo (los mercados de tiros a puerta siguen con el actual).
    activo: bool = True
    sigma: float = 0.30
    half_life_days: float | None = 60.0
    sigma_liga: float = 0.15
    sigma_campo: float = 0.10


@dataclass(frozen=True)
class FittedSot:
    hyper: SotHyper
    trained_until: datetime
    n_matches: int
    params: Parametros
    #: Tamaño ``r`` de la binomial negativa (``None`` = Poisson).
    r: float | None

    def medias(self, sport_key: str, home_id: int, away_id: int) -> tuple[float, float]:
        """Tiros a puerta esperados (local, visitante). Equipo sin datos → media de su liga."""
        eh, ea = self.params.log_lambdas(sport_key, home_id, away_id)
        return math.exp(eh), math.exp(ea)


def estimar_r(ys: np.ndarray, mus: np.ndarray, w: np.ndarray | None = None) -> float | None:
    """Tamaño de la binomial negativa por momentos: ``φ = Σ((y−μ)² − μ) / Σμ²``, ``r = 1/φ``.

    ``None`` si no hay sobredispersión (``φ ≤ 0``): el Poisson ya basta.
    """
    w = np.ones_like(mus) if w is None else w
    num = float(np.sum(w * ((ys - mus) ** 2 - mus)))
    den = float(np.sum(w * mus ** 2))
    if den <= 0 or num <= 0:
        return None
    return max(R_MIN, den / num)


def fit_sot(
    matches: Sequence[TrainingMatch], cutoff: datetime, hyper: SotHyper | None = None,
) -> FittedSot | None:
    """Ajusta los tiros a puerta con los partidos anteriores a ``cutoff`` que los tienen.

    ``None`` si el modelo está desactivado o hay menos de :data:`MIN_PARTIDOS_SOT` partidos.
    """
    hyper = hyper or SotHyper()
    if not hyper.activo:
        return None
    assert_sin_fuga(matches, cutoff)
    con = [m for m in matches if m.home_sot is not None and m.away_sot is not None]
    if len(con) < MIN_PARTIDOS_SOT:
        return None
    equipos = sorted({m.home_id for m in con} | {m.away_id for m in con})
    ligas = sorted({m.sport_key for m in con})
    ie = {t: i for i, t in enumerate(equipos)}
    il = {k: i for i, k in enumerate(ligas)}
    h = np.array([ie[m.home_id] for m in con])
    a = np.array([ie[m.away_id] for m in con])
    li = np.array([il[m.sport_key] for m in con])
    yh = np.array([m.home_sot for m in con], dtype=float)
    ya = np.array([m.away_sot for m in con], dtype=float)
    w = pesos_temporales([m.commence_time for m in con], cutoff, hyper.half_life_days)

    th = _ajustar_poisson(h, a, li, yh, ya, w, len(equipos), len(ligas), hyper)  # type: ignore[arg-type]
    nl, ne = len(ligas), len(equipos)
    params = Parametros(
        mu=float(th[0]),
        campo=float(th[1]),
        liga={k: float(th[2 + i]) for k, i in il.items()},
        campo_liga={k: float(th[2 + nl + i]) for k, i in il.items()},
        ataque={t: float(th[2 + 2 * nl + i]) for t, i in ie.items()},
        defensa={t: float(th[2 + 2 * nl + ne + i]) for t, i in ie.items()},
    )
    mus = np.array([params.log_lambdas(m.sport_key, m.home_id, m.away_id) for m in con])
    mus = np.exp(mus)
    r = estimar_r(np.concatenate([yh, ya]), np.concatenate([mus[:, 0], mus[:, 1]]), np.concatenate([w, w]))
    return FittedSot(hyper=hyper, trained_until=cutoff, n_matches=len(con), params=params, r=r)


def p_mas_de(media: float, linea: float, r: float | None) -> float:
    """P(tiros a puerta > ``linea``) con media ``media`` (binomial negativa de tamaño ``r``, o
    Poisson si ``r`` es ``None``). Con línea entera, el empate (push) no cuenta como "más"."""
    if media <= 0:
        return 0.0
    k = math.floor(linea)  # P(X > línea) = 1 − P(X ≤ ⌊línea⌋)
    if r is None:
        return float(poisson.sf(k, media))
    return float(nbinom.sf(k, r, r / (r + media)))


def p_igual(media: float, valor: int, r: float | None) -> float:
    """P(tiros a puerta = ``valor``) (para el push de las líneas enteras)."""
    if media <= 0:
        return 1.0 if valor == 0 else 0.0
    if r is None:
        return float(poisson.pmf(valor, media))
    return float(nbinom.pmf(valor, r, r / (r + media)))


def r_suma(r: float | None, m1: float, m2: float) -> float | None:
    """Tamaño aproximado de la suma de dos binomiales negativas independientes con el mismo
    ``r`` y medias ``m1``, ``m2`` (igualando la varianza: ``Var = μ + (m1² + m2²)/r``)."""
    if r is None:
        return None
    total = m1 + m2
    return r * total ** 2 / (m1 ** 2 + m2 ** 2) if total > 0 else r


__all__ = [
    "FittedSot",
    "MIN_PARTIDOS_SOT",
    "SotHyper",
    "estimar_r",
    "fit_sot",
    "p_igual",
    "p_mas_de",
    "r_suma",
]
