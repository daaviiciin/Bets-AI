"""Métricas de calibración para el modelo Poisson.

Proporciona:

* :class:`CalibrationSample` — un partido jugado con su predicción y resultado
  real.
* :func:`compute_metrics` — Brier 1X2 / Over-Under / BTTS, log-loss y accuracy.
* :func:`reliability_bins` — bineado para *reliability diagram*.
* :func:`grid_search_home_advantage` — barrido del único hiperparámetro libre
  del modelo.

El módulo es **puro**: no toca BD ni red. Recibe muestras, devuelve números.

Decisiones de diseño
--------------------

* **Brier multi-clase**: usamos la versión "distancia cuadrática a one-hot",
  promediada sobre todas las clases (1X2 → 3 clases, BTTS / OU → 2 clases).
  Para 1X2 el Brier de un modelo "siempre empate" da ≈ 0.667; un modelo
  perfecto da 0.
* **Log-loss**: clipamos probabilidades a ``[1e-12, 1-1e-12]`` para evitar
  ``-inf`` cuando el modelo asigna probabilidad cero al outcome real.
* **Calibración por bin**: agrupamos las predicciones en bins de probabilidad
  y comparamos *p_predicha* media vs *frecuencia observada*. Un modelo bien
  calibrado tiene puntos cerca de la diagonal.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from backend.services import poisson_model as pm

EPS = 1e-12


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationSample:
    """Un partido jugado con su predicción y resultado real."""

    home_team: str
    away_team: str
    home_score: int
    away_score: int
    expectation: pm.MatchExpectation

    @property
    def outcome_1x2(self) -> str:
        if self.home_score > self.away_score:
            return "H"
        if self.home_score == self.away_score:
            return "D"
        return "A"

    @property
    def total_goals(self) -> int:
        return self.home_score + self.away_score

    @property
    def btts_yes(self) -> bool:
        return self.home_score > 0 and self.away_score > 0


@dataclass(frozen=True)
class ReliabilityBin:
    """Bin de un *reliability diagram*."""

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_frequency: float


@dataclass
class CalibrationReport:
    """Resumen de métricas para un conjunto de muestras."""

    n_samples: int = 0
    # Multi-clase
    brier_1x2: float = 0.0
    log_loss_1x2: float = 0.0
    accuracy_1x2: float = 0.0
    # Binarios
    brier_btts: float = 0.0
    log_loss_btts: float = 0.0
    brier_over_25: float = 0.0
    log_loss_over_25: float = 0.0
    # Goles
    mae_total_goals: float = 0.0
    mae_home_goals: float = 0.0
    mae_away_goals: float = 0.0
    # Frecuencias observadas (control)
    observed_home_rate: float = 0.0
    observed_draw_rate: float = 0.0
    observed_away_rate: float = 0.0
    observed_over_25_rate: float = 0.0
    observed_btts_rate: float = 0.0
    # Reliability diagrams
    reliability_home_win: list[ReliabilityBin] = field(default_factory=list)
    reliability_over_25: list[ReliabilityBin] = field(default_factory=list)

    def as_dict(self) -> dict[str, float]:
        return {
            "n_samples": self.n_samples,
            "brier_1x2": self.brier_1x2,
            "log_loss_1x2": self.log_loss_1x2,
            "accuracy_1x2": self.accuracy_1x2,
            "brier_btts": self.brier_btts,
            "log_loss_btts": self.log_loss_btts,
            "brier_over_25": self.brier_over_25,
            "log_loss_over_25": self.log_loss_over_25,
            "mae_total_goals": self.mae_total_goals,
            "mae_home_goals": self.mae_home_goals,
            "mae_away_goals": self.mae_away_goals,
            "observed_home_rate": self.observed_home_rate,
            "observed_draw_rate": self.observed_draw_rate,
            "observed_away_rate": self.observed_away_rate,
            "observed_over_25_rate": self.observed_over_25_rate,
            "observed_btts_rate": self.observed_btts_rate,
        }


# ---------------------------------------------------------------------------
# Ayudantes numéricos
# ---------------------------------------------------------------------------


def _clip(p: float) -> float:
    return min(max(p, EPS), 1 - EPS)


def _brier_multiclass(probs: Sequence[float], one_hot: Sequence[int]) -> float:
    """Brier multi-clase = suma de cuadrados, promediado por clase.

    Para 3 clases [H, D, A], si el partido fue empate one_hot=[0,1,0] y el
    modelo predijo [0.5, 0.3, 0.2], el error es (0.5)² + (0.7)² + (0.2)² = 0.78.
    Dividimos por número de clases para que esté en [0, 1].
    """
    return sum((p - y) ** 2 for p, y in zip(probs, one_hot)) / len(probs)


def _brier_binary(p: float, y: int) -> float:
    """Brier binario: simplemente (p - y)²."""
    return (p - y) ** 2


def _log_loss_multiclass(probs: Sequence[float], one_hot: Sequence[int]) -> float:
    """Cross-entropy: ``-sum(y_i * log(p_i))``."""
    return -sum(y * math.log(_clip(p)) for p, y in zip(probs, one_hot) if y)


def _log_loss_binary(p: float, y: int) -> float:
    """Cross-entropy binaria."""
    p = _clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------


def compute_metrics(
    samples: Iterable[CalibrationSample],
    *,
    n_reliability_bins: int = 10,
) -> CalibrationReport:
    """Calcula todas las métricas de calibración."""
    samples = list(samples)
    if not samples:
        return CalibrationReport()

    n = len(samples)
    rep = CalibrationReport(n_samples=n)

    # Para reliability bins
    home_pairs: list[tuple[float, int]] = []
    over25_pairs: list[tuple[float, int]] = []

    sum_brier_1x2 = 0.0
    sum_ll_1x2 = 0.0
    correct_1x2 = 0

    sum_brier_btts = 0.0
    sum_ll_btts = 0.0
    sum_brier_o25 = 0.0
    sum_ll_o25 = 0.0

    sum_mae_total = 0.0
    sum_mae_home = 0.0
    sum_mae_away = 0.0

    home_wins = 0
    draws = 0
    away_wins = 0
    over_25_count = 0
    btts_count = 0

    for s in samples:
        e = s.expectation

        # 1X2
        outcome = s.outcome_1x2
        one_hot_1x2 = (
            int(outcome == "H"),
            int(outcome == "D"),
            int(outcome == "A"),
        )
        probs_1x2 = (e.p_home_win, e.p_draw, e.p_away_win)
        sum_brier_1x2 += _brier_multiclass(probs_1x2, one_hot_1x2)
        sum_ll_1x2 += _log_loss_multiclass(probs_1x2, one_hot_1x2)

        # Predicción argmax
        pred_idx = max(range(3), key=lambda i: probs_1x2[i])
        actual_idx = ("H", "D", "A").index(outcome)
        if pred_idx == actual_idx:
            correct_1x2 += 1

        if outcome == "H":
            home_wins += 1
        elif outcome == "D":
            draws += 1
        else:
            away_wins += 1

        # BTTS
        y_btts = int(s.btts_yes)
        sum_brier_btts += _brier_binary(e.p_btts_yes, y_btts)
        sum_ll_btts += _log_loss_binary(e.p_btts_yes, y_btts)
        btts_count += y_btts

        # Over 2.5
        y_o25 = int(s.total_goals >= 3)
        sum_brier_o25 += _brier_binary(e.p_over_25, y_o25)
        sum_ll_o25 += _log_loss_binary(e.p_over_25, y_o25)
        over_25_count += y_o25

        # MAE goles
        sum_mae_total += abs((e.lambda_home + e.lambda_away) - s.total_goals)
        sum_mae_home += abs(e.lambda_home - s.home_score)
        sum_mae_away += abs(e.lambda_away - s.away_score)

        home_pairs.append((e.p_home_win, int(outcome == "H")))
        over25_pairs.append((e.p_over_25, y_o25))

    rep.brier_1x2 = sum_brier_1x2 / n
    rep.log_loss_1x2 = sum_ll_1x2 / n
    rep.accuracy_1x2 = correct_1x2 / n
    rep.brier_btts = sum_brier_btts / n
    rep.log_loss_btts = sum_ll_btts / n
    rep.brier_over_25 = sum_brier_o25 / n
    rep.log_loss_over_25 = sum_ll_o25 / n
    rep.mae_total_goals = sum_mae_total / n
    rep.mae_home_goals = sum_mae_home / n
    rep.mae_away_goals = sum_mae_away / n
    rep.observed_home_rate = home_wins / n
    rep.observed_draw_rate = draws / n
    rep.observed_away_rate = away_wins / n
    rep.observed_over_25_rate = over_25_count / n
    rep.observed_btts_rate = btts_count / n

    rep.reliability_home_win = reliability_bins(home_pairs, n_bins=n_reliability_bins)
    rep.reliability_over_25 = reliability_bins(over25_pairs, n_bins=n_reliability_bins)

    return rep


def reliability_bins(
    pairs: Sequence[tuple[float, int]],
    *,
    n_bins: int = 10,
) -> list[ReliabilityBin]:
    """Agrupa predicciones en bins de probabilidad para *reliability diagram*.

    Cada bin reporta:

    * número de muestras,
    * probabilidad media predicha,
    * frecuencia observada (cuántas veces ocurrió de verdad).

    Un modelo bien calibrado tiene ``mean_predicted ≈ observed_frequency``
    en todos los bins (puntos cerca de la diagonal).
    """
    if not pairs or n_bins <= 0:
        return []

    width = 1.0 / n_bins
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in pairs:
        idx = min(int(p / width), n_bins - 1)
        bins[idx].append((p, y))

    out: list[ReliabilityBin] = []
    for i, bucket in enumerate(bins):
        lower = i * width
        upper = (i + 1) * width
        if not bucket:
            continue
        mean_p = statistics.fmean(p for p, _ in bucket)
        freq = statistics.fmean(y for _, y in bucket)
        out.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                mean_predicted=mean_p,
                observed_frequency=freq,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Grid search del único hiperparámetro
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridSearchResult:
    """Resultado de evaluar el modelo con un combo (home_advantage, rho)."""

    home_advantage: float
    report: CalibrationReport
    dixon_coles_rho: float = 0.0

    @property
    def loss(self) -> float:
        """Loss compuesta: priorizamos calibración 1X2 (Brier) por encima de
        accuracy, y añadimos un toque de Brier OU 2.5 / BTTS para no sobreajustar
        a la primera métrica."""
        return (
            self.report.brier_1x2
            + 0.5 * self.report.brier_over_25
            + 0.5 * self.report.brier_btts
        )


def grid_search_home_advantage(
    samples_factory,
    *,
    values: Sequence[float] = (1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30),
) -> list[GridSearchResult]:
    """Evalúa el modelo con cada valor de ``home_advantage`` (rho fijo en 0).

    ``samples_factory`` es un callable que recibe un valor de home_advantage y
    devuelve la lista de :class:`CalibrationSample` usando ESE valor para las
    predicciones.
    """
    results: list[GridSearchResult] = []
    for ha in values:
        samples = samples_factory(ha)
        report = compute_metrics(samples)
        results.append(GridSearchResult(
            home_advantage=ha, report=report, dixon_coles_rho=0.0,
        ))
    return results


def grid_search_combined(
    samples_factory,
    *,
    home_advantage_values: Sequence[float] = (1.05, 1.10, 1.15, 1.20),
    dixon_coles_values: Sequence[float] = (-0.15, -0.10, -0.05, 0.0, 0.05, 0.10),
) -> list[GridSearchResult]:
    """Grid search combinado sobre ``home_advantage`` y ``dixon_coles_rho``.

    ``samples_factory(ha, rho)`` debe devolver la lista de
    :class:`CalibrationSample` predichos con esos hiperparámetros.

    Coste = ``len(ha) × len(rho)`` evaluaciones. Para 4 × 6 = 24 sobre ~1500
    partidos quedan 36k predicciones, que en CPython tardan menos de 5 s.
    """
    results: list[GridSearchResult] = []
    for ha in home_advantage_values:
        for rho in dixon_coles_values:
            samples = samples_factory(ha, rho)
            report = compute_metrics(samples)
            results.append(GridSearchResult(
                home_advantage=ha, dixon_coles_rho=rho, report=report,
            ))
    return results
