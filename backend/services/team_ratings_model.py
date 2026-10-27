"""Ratings de equipo Dixon–Coles con encogimiento (fase 3, bloque B4). Puro: numpy + scipy.

Para un partido de la liga ``l`` entre ``i`` (local) y ``j`` (visitante)::

    log λ_local     = μ + μ_l + campo + campo_l + ataque_i − defensa_j
    log λ_visitante = μ + μ_l           + ataque_j − defensa_i

- **Encogimiento (ridge):** ataque y defensa llevan un previo N(0, σ²). Con 8–10 partidos por
  equipo es la regresión a la media que le falta al modelo actual: un equipo con pocos partidos
  (o con una racha) se queda cerca de la media de su liga en vez de dispararse. ``μ_l`` y
  ``campo_l`` se encogen hacia el global (``sigma_liga``, ``sigma_campo``).
- **Fuerza del rival:** el ajuste es conjunto, así que marcar 3 goles al último no vale lo mismo
  que marcárselos al primero (las medias de temporada no lo distinguen).
- **UEFA:** los equipos comparten ataque y defensa entre su liga y la competición europea, que
  solo añade su ``μ_l``; los partidos europeos conectan las ligas entre sí.
- **Decaimiento temporal:** cada partido pesa 0,5^(días / semivida); ``None`` = todos igual.
- **xG (opcional):** el mismo ajuste con el xG como respuesta (quasi-Poisson) y la mezcla
  ``log λ = w·log λ_goles + (1 − w)·log λ_xG``; ``w = 1`` es el modelo sin xG.

Ajuste en dos pasos: Poisson penalizado (convexo, L-BFGS-B con gradiente exacto) y después el ρ
de Dixon–Coles por máxima verosimilitud con las λ ya fijadas (como ``goalmodel``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from backend.services import poisson_model as pm
from backend.services.model_dataset import TrainingMatch, assert_sin_fuga

#: Previo de la cuota de goles de la 2ª parte de cada liga: equivale a este nº de goles con la
#: media global (con ~250 goles por liga, la liga pesa ~80 %).
SECOND_HALF_PRIOR_GOALS = 60.0
#: Límites de ρ (la calibración real suele dar |ρ| < 0,2).
RHO_LIMITE = 0.25


@dataclass(frozen=True)
class RatingsHyper:
    #: Desviación del previo de ataque y defensa (log-goles). Menor = más encogimiento.
    sigma: float = 0.35
    half_life_days: float | None = None
    sigma_liga: float = 0.15
    sigma_campo: float = 0.10
    #: Peso de los goles frente al xG (1 = solo goles).
    xg_weight: float = 1.0


@dataclass(frozen=True)
class Parametros:
    """Un juego de parámetros ajustados (con goles o con xG como respuesta)."""

    mu: float
    campo: float
    liga: dict[str, float]
    campo_liga: dict[str, float]
    ataque: dict[int, float]
    defensa: dict[int, float]

    def log_lambdas(self, sport_key: str, home_id: int, away_id: int) -> tuple[float, float]:
        base = self.mu + self.liga.get(sport_key, 0.0)
        eh = base + self.campo + self.campo_liga.get(sport_key, 0.0)
        eh += self.ataque.get(home_id, 0.0) - self.defensa.get(away_id, 0.0)
        ea = base + self.ataque.get(away_id, 0.0) - self.defensa.get(home_id, 0.0)
        return eh, ea


@dataclass(frozen=True)
class FittedRatings:
    hyper: RatingsHyper
    trained_until: datetime
    n_matches: int
    goles: Parametros
    xg: Parametros | None
    rho: float
    partidos_por_equipo: dict[int, int] = field(default_factory=dict)
    #: Cuota de goles de la 2ª parte por liga (encogida a la global, en ``"_global"``).
    second_half_share: dict[str, float] = field(default_factory=dict)

    def lambdas(self, sport_key: str, home_id: int, away_id: int) -> tuple[float, float]:
        """Goles esperados (local, visitante). Equipo sin partidos → media de su liga."""
        gh, ga = self.goles.log_lambdas(sport_key, home_id, away_id)
        w = self.hyper.xg_weight
        if self.xg is not None and w < 1.0:
            xh, xa = self.xg.log_lambdas(sport_key, home_id, away_id)
            gh, ga = w * gh + (1 - w) * xh, w * ga + (1 - w) * xa
        return math.exp(gh), math.exp(ga)

    def partidos(self, team_id: int) -> int:
        return self.partidos_por_equipo.get(team_id, 0)

    def share_2a_parte(self, sport_key: str) -> float:
        return self.second_half_share.get(
            sport_key, self.second_half_share.get("_global", pm.SECOND_HALF_GOAL_SHARE),
        )

    # -- JSON (para guardar el ajuste en ``model_fits``) -------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def params(p: Parametros | None) -> dict[str, Any] | None:
            if p is None:
                return None
            d = asdict(p)
            d["ataque"] = {str(k): v for k, v in p.ataque.items()}
            d["defensa"] = {str(k): v for k, v in p.defensa.items()}
            return d

        return {
            "hyper": asdict(self.hyper),
            "trained_until": self.trained_until.isoformat(),
            "n_matches": self.n_matches,
            "goles": params(self.goles),
            "xg": params(self.xg),
            "rho": self.rho,
            "partidos_por_equipo": {str(k): v for k, v in self.partidos_por_equipo.items()},
            "second_half_share": dict(self.second_half_share),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FittedRatings:
        def params(p: dict[str, Any] | None) -> Parametros | None:
            if p is None:
                return None
            return Parametros(
                mu=p["mu"], campo=p["campo"], liga=dict(p["liga"]),
                campo_liga=dict(p["campo_liga"]),
                ataque={int(k): v for k, v in p["ataque"].items()},
                defensa={int(k): v for k, v in p["defensa"].items()},
            )

        goles = params(d["goles"])
        assert goles is not None
        return cls(
            hyper=RatingsHyper(**d["hyper"]),
            trained_until=datetime.fromisoformat(d["trained_until"]),
            n_matches=int(d["n_matches"]),
            goles=goles,
            xg=params(d.get("xg")),
            rho=float(d["rho"]),
            partidos_por_equipo={int(k): int(v) for k, v in d["partidos_por_equipo"].items()},
            second_half_share={k: float(v) for k, v in d.get("second_half_share", {}).items()},
        )


# ---------------------------------------------------------------------------
# Ajuste
# ---------------------------------------------------------------------------


def pesos_temporales(fechas: Sequence[datetime], cutoff: datetime, half_life_days: float | None) -> np.ndarray:
    """0,5^(días hasta el corte / semivida); todos 1 sin semivida."""
    if half_life_days is None:
        return np.ones(len(fechas))
    dias = np.array([(cutoff - f).total_seconds() / 86400.0 for f in fechas])
    return np.power(0.5, np.maximum(dias, 0.0) / half_life_days)


def _ajustar_poisson(
    h: np.ndarray, a: np.ndarray, li: np.ndarray, yh: np.ndarray, ya: np.ndarray,
    w: np.ndarray, n_equipos: int, n_ligas: int, hyper: RatingsHyper,
) -> np.ndarray:
    """Poisson penalizado; devuelve θ = [μ, campo, μ_l…, campo_l…, ataque…, defensa…]."""
    nl, ne = n_ligas, n_equipos
    s_l, s_c, s_t = hyper.sigma_liga ** 2, hyper.sigma_campo ** 2, hyper.sigma ** 2

    def partes(th: np.ndarray):
        mu, campo = th[0], th[1]
        liga = th[2:2 + nl]
        campo_l = th[2 + nl:2 + 2 * nl]
        att = th[2 + 2 * nl:2 + 2 * nl + ne]
        de = th[2 + 2 * nl + ne:]
        return mu, campo, liga, campo_l, att, de

    def f(th: np.ndarray) -> tuple[float, np.ndarray]:
        mu, campo, liga, campo_l, att, de = partes(th)
        eh = mu + campo + liga[li] + campo_l[li] + att[h] - de[a]
        ea = mu + liga[li] + att[a] - de[h]
        lh, la = np.exp(eh), np.exp(ea)
        nll = float(np.sum(w * (lh - yh * eh)) + np.sum(w * (la - ya * ea)))
        nll += float(np.sum(att ** 2) + np.sum(de ** 2)) / (2 * s_t)
        nll += float(np.sum(liga ** 2)) / (2 * s_l) + float(np.sum(campo_l ** 2)) / (2 * s_c)
        gh, ga = w * (lh - yh), w * (la - ya)
        grad = np.empty_like(th)
        grad[0] = gh.sum() + ga.sum()
        grad[1] = gh.sum()
        grad[2:2 + nl] = np.bincount(li, gh + ga, nl) + liga / s_l
        grad[2 + nl:2 + 2 * nl] = np.bincount(li, gh, nl) + campo_l / s_c
        grad[2 + 2 * nl:2 + 2 * nl + ne] = (
            np.bincount(h, gh, ne) + np.bincount(a, ga, ne) + att / s_t
        )
        grad[2 + 2 * nl + ne:] = -np.bincount(a, gh, ne) - np.bincount(h, ga, ne) + de / s_t
        return nll, grad

    th0 = np.zeros(2 + 2 * nl + 2 * ne)
    media = max(float(np.sum(w * (yh + ya)) / (2 * np.sum(w))), 0.05)
    th0[0] = math.log(media)
    res = minimize(f, th0, jac=True, method="L-BFGS-B", options={"maxiter": 2000})
    return res.x


def _tau(x: np.ndarray, y: np.ndarray, lh: np.ndarray, la: np.ndarray, rho: float) -> np.ndarray:
    """Factor de Dixon–Coles (misma convención que ``poisson_model._apply_dixon_coles``)."""
    t = np.ones_like(lh)
    t = np.where((x == 0) & (y == 0), 1.0 - lh * la * rho, t)
    t = np.where((x == 0) & (y == 1), 1.0 + lh * rho, t)
    t = np.where((x == 1) & (y == 0), 1.0 + la * rho, t)
    t = np.where((x == 1) & (y == 1), 1.0 - rho, t)
    return t


def estimar_rho(x: np.ndarray, y: np.ndarray, lh: np.ndarray, la: np.ndarray, w: np.ndarray) -> float:
    """ρ de máxima verosimilitud con las λ fijas, dentro del rango en que τ > 0."""
    bajos = (x <= 1) & (y <= 1)
    if not bajos.any():
        return 0.0
    xb, yb, lhb, lab, wb = x[bajos], y[bajos], lh[bajos], la[bajos], w[bajos]
    lo = max(-RHO_LIMITE, -0.99 / float(lhb.max()), -0.99 / float(lab.max()))
    hi = min(RHO_LIMITE, 0.99 / float((lhb * lab).max()), 0.99)

    def nll(rho: float) -> float:
        t = _tau(xb, yb, lhb, lab, rho)
        if np.any(t <= 0):
            return math.inf
        return -float(np.sum(wb * np.log(t)))

    res = minimize_scalar(nll, bounds=(lo, hi), method="bounded")
    return float(res.x)


def _cuotas_2a_parte(matches: Sequence[TrainingMatch]) -> dict[str, float]:
    total = {"_global": [0, 0]}
    for m in matches:
        if m.goals_1h is None or m.goals_2h is None:
            continue
        for k in (m.sport_key, "_global"):
            acc = total.setdefault(k, [0, 0])
            acc[0] += m.goals_1h + m.goals_2h
            acc[1] += m.goals_2h
    g_tot, g_2 = total["_global"]
    s_global = g_2 / g_tot if g_tot else pm.SECOND_HALF_GOAL_SHARE
    out = {"_global": s_global}
    for k, (g, g2) in total.items():
        if k != "_global":
            out[k] = (g2 + SECOND_HALF_PRIOR_GOALS * s_global) / (g + SECOND_HALF_PRIOR_GOALS)
    return out


def fit_ratings(matches: Sequence[TrainingMatch], cutoff: datetime, hyper: RatingsHyper) -> FittedRatings:
    """Ajusta los ratings con los partidos anteriores a ``cutoff`` (falla si hay alguno posterior)."""
    assert_sin_fuga(matches, cutoff)
    if not matches:
        raise ValueError("Sin partidos para ajustar el modelo")
    equipos = sorted({m.home_id for m in matches} | {m.away_id for m in matches})
    ligas = sorted({m.sport_key for m in matches})
    ie = {t: i for i, t in enumerate(equipos)}
    il = {k: i for i, k in enumerate(ligas)}

    h = np.array([ie[m.home_id] for m in matches])
    a = np.array([ie[m.away_id] for m in matches])
    li = np.array([il[m.sport_key] for m in matches])
    x = np.array([m.home_goals for m in matches], dtype=float)
    y = np.array([m.away_goals for m in matches], dtype=float)
    w = pesos_temporales([m.commence_time for m in matches], cutoff, hyper.half_life_days)

    def a_parametros(th: np.ndarray) -> Parametros:
        nl, ne = len(ligas), len(equipos)
        return Parametros(
            mu=float(th[0]),
            campo=float(th[1]),
            liga={k: float(th[2 + i]) for k, i in il.items()},
            campo_liga={k: float(th[2 + nl + i]) for k, i in il.items()},
            ataque={t: float(th[2 + 2 * nl + i]) for t, i in ie.items()},
            defensa={t: float(th[2 + 2 * nl + ne + i]) for t, i in ie.items()},
        )

    goles = a_parametros(_ajustar_poisson(h, a, li, x, y, w, len(equipos), len(ligas), hyper))

    xg_params: Parametros | None = None
    if hyper.xg_weight < 1.0:
        con_xg = np.array([m.home_xg is not None for m in matches])
        if con_xg.sum() >= 50:
            xh = np.array([m.home_xg or 0.0 for m in matches])
            xa = np.array([m.away_xg or 0.0 for m in matches])
            xg_params = a_parametros(_ajustar_poisson(
                h[con_xg], a[con_xg], li[con_xg], xh[con_xg], xa[con_xg], w[con_xg],
                len(equipos), len(ligas), hyper,
            ))

    partidos: dict[int, int] = {}
    for m in matches:
        partidos[m.home_id] = partidos.get(m.home_id, 0) + 1
        partidos[m.away_id] = partidos.get(m.away_id, 0) + 1

    sin_rho = FittedRatings(
        hyper=hyper, trained_until=cutoff, n_matches=len(matches), goles=goles, xg=xg_params,
        rho=0.0, partidos_por_equipo=partidos,
    )
    lam = np.array([sin_rho.lambdas(m.sport_key, m.home_id, m.away_id) for m in matches])
    rho = estimar_rho(x, y, lam[:, 0], lam[:, 1], w)
    return FittedRatings(
        hyper=hyper, trained_until=cutoff, n_matches=len(matches), goles=goles, xg=xg_params,
        rho=rho, partidos_por_equipo=partidos, second_half_share=_cuotas_2a_parte(matches),
    )


__all__ = [
    "FittedRatings",
    "Parametros",
    "RatingsHyper",
    "estimar_rho",
    "fit_ratings",
    "pesos_temporales",
]
