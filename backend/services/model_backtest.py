"""Backtest walk-forward del modelo v2 y sus bases de comparación (fase 3, bloque B7).

Para cada día D con partidos: se ajusta con los partidos que empezaron **antes** de las 00:00 UTC
de D y se predicen los de D (sin fuga: ``fit_ratings`` falla si le llega un partido posterior al
corte). Cada partido da varios **sucesos** binarios por familia (gana el local, más de 2,5…) y
cada suceso se evalúa con log-loss, Brier y ECE.

Bases de comparación, siempre sobre los mismos sucesos:

- ``legacy``: el modelo actual (medias de temporada de la liga sin encoger, campo 1,10), con
  los partidos anteriores a D.
- ``base``: la frecuencia del suceso en los partidos anteriores a D.
- ``mercado``: la última cuota de Winamax antes del saque (``odds_history``), sin margen (método
  "power"). Solo hay en los partidos con historial de cuotas.

Todo es puro salvo :func:`cargar_cierres`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from typing import TYPE_CHECKING

from backend.services import poisson_model as pm
from backend.services.market_probabilities import MatchPrediction, mezclar, sin_margen_power
from backend.services.model_calibration_v2 import (
    Calibracion,
    ajustar_platt,
    brier,
    ece,
    log_loss,
)
from backend.services.model_dataset import UEFA_SPORT_KEYS, TrainingMatch
from backend.services.team_ratings_model import RatingsHyper, fit_ratings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

LINEAS_TOTALES = (1.5, 2.5, 3.5)
LINEAS_EQUIPO = (0.5, 1.5)
FAMILIAS_TRES_VIAS = frozenset({"1x2", "highest_half"})
#: Familias con las que se elige el modelo (las de los picks de hoy).
FAMILIAS_PRINCIPALES = ("1x2", "totals", "team_totals", "btts", "highest_half")


@dataclass(frozen=True)
class Evento:
    familia: str
    #: Suceso concreto: "1x2:H", "totals:2.5", "team:home:1.5", "btts", "half:2h"…
    clave: str
    match_id: int
    fecha: date
    p: float
    y: int


def resultado(clave: str, m: TrainingMatch) -> int | None:
    """1 si el suceso ``clave`` ocurrió en ``m``, 0 si no; ``None`` si falta el dato."""
    hg, ag = m.home_goals, m.away_goals
    fam, _, resto = clave.partition(":")
    if fam == "1x2":
        real = "H" if hg > ag else ("A" if ag > hg else "D")
        return int(real == resto)
    if fam == "totals":
        return int(hg + ag > float(resto))
    if fam == "team":
        lado, _, linea = resto.partition(":")
        return int((hg if lado == "home" else ag) > float(linea))
    if fam == "btts":
        return int(hg >= 1 and ag >= 1)
    if fam == "half":
        if m.goals_1h is None or m.goals_2h is None:
            return None
        real = "1h" if m.goals_1h > m.goals_2h else ("2h" if m.goals_2h > m.goals_1h else "tie")
        return int(real == resto)
    return None


def probabilidades_de_sucesos(pred: MatchPrediction) -> dict[str, tuple[str, float]]:
    """``clave → (familia, P sin calibrar)`` de todos los sucesos que se evalúan."""
    g = pred.rejilla
    n = g.shape[0]
    out: dict[str, tuple[str, float]] = {}
    ph, pd, pa = pred.p_1x2
    out.update({"1x2:H": ("1x2", ph), "1x2:D": ("1x2", pd), "1x2:A": ("1x2", pa)})
    for linea in LINEAS_TOTALES:
        p = sum(g[i, j] for i in range(n) for j in range(n) if i + j > linea)
        out[f"totals:{linea}"] = ("totals", float(p))
    for linea in LINEAS_EQUIPO:
        out[f"team:home:{linea}"] = ("team_totals", float(g[[i for i in range(n) if i > linea], :].sum()))
        out[f"team:away:{linea}"] = ("team_totals", float(g[:, [j for j in range(n) if j > linea]].sum()))
    out["btts"] = ("btts", float(g[1:, 1:].sum()))
    p1, pig, p2 = pred.p_mitades
    out.update({"half:1h": ("highest_half", p1), "half:tie": ("highest_half", pig), "half:2h": ("highest_half", p2)})
    return out


def _eventos(pred: MatchPrediction, m: TrainingMatch) -> list[Evento]:
    out = []
    for clave, (fam, p) in probabilidades_de_sucesos(pred).items():
        y = resultado(clave, m)
        if y is not None:
            out.append(Evento(fam, clave, m.match_id, m.commence_time.date(), p, y))
    return out


def _dias(matches: Sequence[TrainingMatch], desde: datetime) -> list[date]:
    return sorted({m.commence_time.date() for m in matches if m.commence_time >= desde})


def _corte(dia: date) -> datetime:
    return datetime.combine(dia, time(0), tzinfo=timezone.utc)


def walk_forward_v2(
    matches: Sequence[TrainingMatch], hyper: RatingsHyper, *, desde: datetime, min_partidos: int = 3,
) -> list[Evento]:
    """Sucesos predichos por el v2, cada día con un ajuste nuevo hasta las 00:00 UTC de ese día."""
    eventos: list[Evento] = []
    for dia in _dias(matches, desde):
        cutoff = _corte(dia)
        train = [m for m in matches if m.commence_time < cutoff]
        if len(train) < 50:
            continue
        fit = fit_ratings(train, cutoff, hyper)
        for m in matches:
            if m.commence_time.date() != dia:
                continue
            if fit.partidos(m.home_id) < min_partidos or fit.partidos(m.away_id) < min_partidos:
                continue
            lh, la = fit.lambdas(m.sport_key, m.home_id, m.away_id)
            pred = MatchPrediction(lh, la, fit.rho, fit.share_2a_parte(m.sport_key))
            eventos.extend(_eventos(pred, m))
    return eventos


def walk_forward_legacy(
    matches: Sequence[TrainingMatch], *, desde: datetime, min_partidos: int = 3,
) -> list[Evento]:
    """Sucesos del modelo actual: medias de temporada de la liga, sin encoger (solo ligas)."""
    eventos: list[Evento] = []
    for dia in _dias(matches, desde):
        cutoff = _corte(dia)
        por_liga: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
        for m in matches:
            if m.commence_time < cutoff and m.sport_key not in UEFA_SPORT_KEYS:
                acc = por_liga[m.sport_key]
                for t, gf, ga in ((m.home_id, m.home_goals, m.away_goals), (m.away_id, m.away_goals, m.home_goals)):
                    acc[t][0] += 1
                    acc[t][1] += gf
                    acc[t][2] += ga
        for m in matches:
            if m.commence_time.date() != dia or m.sport_key in UEFA_SPORT_KEYS:
                continue
            acc = por_liga.get(m.sport_key, {})
            h, a = acc.get(m.home_id), acc.get(m.away_id)
            if not h or not a or h[0] < min_partidos or a[0] < min_partidos:
                continue
            liga = [pm.TeamLeagueAverages(v[0], v[1] / v[0], v[2] / v[0]) for v in acc.values() if v[0]]
            exp = pm.predict(
                home=pm.TeamLeagueAverages(h[0], h[1] / h[0], h[2] / h[0]),
                away=pm.TeamLeagueAverages(a[0], a[1] / a[0], a[2] / a[0]),
                league_teams=liga,
            )
            eventos.extend(_eventos(MatchPrediction(exp.lambda_home, exp.lambda_away), m))
    return eventos


def eventos_base(matches: Sequence[TrainingMatch], referencia: Iterable[Evento]) -> list[Evento]:
    """La frecuencia de cada suceso en los partidos anteriores a su día (Laplace +1/+2)."""
    out = []
    cache: dict[tuple[date, str], float] = {}
    for ev in referencia:
        k = (ev.fecha, ev.clave)
        if k not in cache:
            cutoff = _corte(ev.fecha)
            ys = [resultado(ev.clave, m) for m in matches if m.commence_time < cutoff]
            ys = [y for y in ys if y is not None]
            cache[k] = (sum(ys) + 1) / (len(ys) + 2)
        out.append(replace(ev, p=cache[k]))
    return out


# ---------------------------------------------------------------------------
# Calibración y métricas
# ---------------------------------------------------------------------------


def ajustar_calibracion(eventos: Sequence[Evento]) -> Calibracion:
    por_familia: dict[str, tuple[list[float], list[int]]] = defaultdict(lambda: ([], []))
    for ev in eventos:
        ps, ys = por_familia[ev.familia]
        ps.append(ev.p)
        ys.append(ev.y)
    return Calibracion({fam: ajustar_platt(ps, ys) for fam, (ps, ys) in por_familia.items()})


def calibrar_eventos(eventos: Sequence[Evento], cal: Calibracion) -> list[Evento]:
    """Aplica la calibración; en las familias de tres resultados renormaliza por partido."""
    out = [replace(ev, p=cal.aplicar(ev.familia, ev.p)) for ev in eventos]
    grupos: dict[tuple[int, str], list[int]] = defaultdict(list)
    for i, ev in enumerate(out):
        if ev.familia in FAMILIAS_TRES_VIAS:
            grupos[(ev.match_id, ev.familia)].append(i)
    for idx in grupos.values():
        total = sum(out[i].p for i in idx)
        if total > 0:
            for i in idx:
                out[i] = replace(out[i], p=out[i].p / total)
    return out


@dataclass(frozen=True)
class Metricas:
    n: int
    log_loss: float
    brier: float
    ece: float


def metricas(eventos: Sequence[Evento]) -> Metricas:
    ps, ys = [e.p for e in eventos], [e.y for e in eventos]
    return Metricas(len(ps), log_loss(ps, ys), brier(ps, ys), ece(ps, ys))


def por_familia(eventos: Sequence[Evento]) -> dict[str, Metricas]:
    grupos: dict[str, list[Evento]] = defaultdict(list)
    for ev in eventos:
        grupos[ev.familia].append(ev)
    return {fam: metricas(evs) for fam, evs in sorted(grupos.items())}


def comunes(*listas: Sequence[Evento]) -> list[list[Evento]]:
    """Las mismas listas, quedándose solo con los sucesos (partido, clave) presentes en todas."""
    claves = set.intersection(*({(e.match_id, e.clave) for e in lst} for lst in listas))
    return [[e for e in lst if (e.match_id, e.clave) in claves] for lst in listas]


def puntuacion(eventos: Sequence[Evento], familias: Iterable[str] = FAMILIAS_PRINCIPALES) -> float:
    """Log-loss medio de las familias (cada familia pesa lo mismo)."""
    m = por_familia([e for e in eventos if e.familia in set(familias)])
    return sum(v.log_loss for v in m.values()) / len(m) if m else math.inf


# ---------------------------------------------------------------------------
# Mercado (cuotas de cierre de Winamax)
# ---------------------------------------------------------------------------


#: Pesos del mercado que se prueban en la mezcla ``(1 − w)·modelo + w·mercado``.
PESOS_MERCADO = tuple(i / 10 for i in range(11))
#: Sucesos con cuota de cierre necesarios para fijar el peso del mercado (si no, 0).
MIN_EVENTOS_MERCADO = 100
#: Para habilitar una familia en los picks: sucesos en la ventana de informe y ECE máximo.
MIN_EVENTOS_FAMILIA = 100
UMBRAL_ECE = 0.05


def mezclar_eventos(
    eventos: Sequence[Evento], cierres: dict[tuple[int, str], float], peso: float,
) -> list[Evento]:
    """Mezcla con el mercado los sucesos que tienen cuota de cierre (el resto, tal cual)."""
    return [
        replace(e, p=mezclar(e.p, cierres.get((e.match_id, e.clave)), peso)) for e in eventos
    ]


def elegir_peso_mercado(
    eventos: Sequence[Evento], cierres: dict[tuple[int, str], float],
) -> tuple[float, dict[float, float]]:
    """Peso del mercado con menor log-loss en los sucesos con cuota de cierre (y la curva)."""
    con = [e for e in eventos if (e.match_id, e.clave) in cierres]
    if len(con) < MIN_EVENTOS_MERCADO:
        return 0.0, {}
    curva = {w: metricas(mezclar_eventos(con, cierres, w)).log_loss for w in PESOS_MERCADO}
    return min(curva, key=lambda w: (curva[w], w)), curva


def familias_habilitadas(
    modelo: dict[str, Metricas], base: dict[str, Metricas],
) -> list[str]:
    """Familias que pueden dar picks: bastantes sucesos, bien calibradas y mejores que la base."""
    return sorted(
        fam for fam, m in modelo.items()
        if m.n >= MIN_EVENTOS_FAMILIA and m.ece <= UMBRAL_ECE
        and fam in base and m.log_loss <= base[fam].log_loss
    )


def debe_promoverse(v2: float, legacy: float, base: float) -> tuple[bool, list[str]]:
    """Puerta de promoción sobre la ventana de informe (log-loss medio por familia)."""
    motivos = []
    if not v2 < legacy:
        motivos.append(f"no mejora al modelo actual ({v2:.4f} frente a {legacy:.4f})")
    if not v2 <= base:
        motivos.append(f"no mejora a la tasa base ({v2:.4f} frente a {base:.4f})")
    return not motivos, motivos


def eventos_mercado(
    cierres: dict[tuple[int, str], float], referencia: Iterable[Evento],
) -> list[Evento]:
    """Sucesos de ``referencia`` con P del mercado (solo los que tienen cuota de cierre)."""
    return [replace(ev, p=cierres[(ev.match_id, ev.clave)]) for ev in referencia
            if (ev.match_id, ev.clave) in cierres]


def cargar_cierres(session: Session, match_ids: Sequence[int]) -> dict[tuple[int, str], float]:
    """Última cuota de Winamax antes del saque, sin margen, por (partido, suceso).

    Cubre 1X2, más/menos de 1,5/2,5/3,5 goles y ambos marcan (los mercados con todas sus
    selecciones en el historial).
    """
    from sqlalchemy import select

    from backend.models import Market, Match, OddsHistory
    from backend.services.market_probabilities import _lados_1x2

    if not match_ids:
        return {}
    filas = session.execute(
        select(
            OddsHistory.match_id, Market.key, OddsHistory.outcome_name, OddsHistory.outcome_point,
            OddsHistory.price, OddsHistory.first_seen_at, Match.commence_time,
        )
        .join(Market, Market.id == OddsHistory.market_id)
        .join(Match, Match.id == OddsHistory.match_id)
        .where(OddsHistory.match_id.in_(list(match_ids)))
        .where(Market.key.in_(["h2h", "totals", "btts"]))
        .where(OddsHistory.player_id.is_(None))
        .where(OddsHistory.first_seen_at < Match.commence_time),
    ).all()
    ultima: dict[tuple[int, str, str, float | None], tuple[datetime, float]] = {}
    for mid, mk, oc, pt, price, first_seen, _ in filas:
        k = (mid, mk, oc, float(pt) if pt is not None else None)
        if k not in ultima or first_seen > ultima[k][0]:
            ultima[k] = (first_seen, float(price))

    equipos = dict(
        (m.id, (m.home_team.name, m.away_team.name))
        for m in session.scalars(select(Match).where(Match.id.in_(list(match_ids)))).all()
    )
    out: dict[tuple[int, str], float] = {}
    por_mercado: dict[tuple[int, str, float | None], dict[str, float]] = defaultdict(dict)
    for (mid, mk, oc, pt), (_, price) in ultima.items():
        por_mercado[(mid, mk, pt)][oc] = price
    for (mid, mk, pt), sel in por_mercado.items():
        if mk == "h2h" and mid in equipos:
            home, away = equipos[mid]
            lados: dict[str, float] = {}
            for oc, price in sel.items():
                ls = _lados_1x2(oc, home, away)
                if ls and len(ls) == 1:
                    lados[next(iter(ls))] = price
            if set(lados) == {"H", "D", "A"}:
                for s, p in zip(("H", "D", "A"), sin_margen_power([lados[x] for x in ("H", "D", "A")])):
                    out[(mid, f"1x2:{s}")] = p
        elif mk == "totals" and pt in LINEAS_TOTALES and {"Over", "Under"} <= set(sel):
            p_over, _ = sin_margen_power([sel["Over"], sel["Under"]])
            out[(mid, f"totals:{pt}")] = p_over
        elif mk == "btts" and {"Yes", "No"} <= set(sel):
            p_si, _ = sin_margen_power([sel["Yes"], sel["No"]])
            out[(mid, "btts")] = p_si
    return out


# ---------------------------------------------------------------------------
# Búsqueda de hiperparámetros
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoRejilla:
    hyper: RatingsHyper
    puntuacion_a: float
    eventos: list[Evento]


def buscar_hiper(
    matches: Sequence[TrainingMatch],
    rejilla: Iterable[RatingsHyper],
    *,
    desde: datetime,
    hasta_a: datetime,
    progreso: Callable[[RatingsHyper, float], None] | None = None,
) -> list[ResultadoRejilla]:
    """Walk-forward de cada combinación; ordena por log-loss en la ventana A (``< hasta_a``)."""
    out = []
    for hyper in rejilla:
        evs = walk_forward_v2(matches, hyper, desde=desde)
        punt = puntuacion([e for e in evs if _corte(e.fecha) < hasta_a])
        if progreso:
            progreso(hyper, punt)
        out.append(ResultadoRejilla(hyper, punt, evs))
    return sorted(out, key=lambda r: r.puntuacion_a)


__all__ = [
    "Evento",
    "FAMILIAS_PRINCIPALES",
    "Metricas",
    "ResultadoRejilla",
    "ajustar_calibracion",
    "buscar_hiper",
    "calibrar_eventos",
    "cargar_cierres",
    "comunes",
    "debe_promoverse",
    "elegir_peso_mercado",
    "eventos_base",
    "eventos_mercado",
    "familias_habilitadas",
    "metricas",
    "mezclar_eventos",
    "por_familia",
    "probabilidades_de_sucesos",
    "puntuacion",
    "resultado",
    "walk_forward_legacy",
    "walk_forward_v2",
]
