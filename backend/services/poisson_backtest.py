"""Walk-forward backtest del modelo Poisson independiente.

Métrica honesta: para cada partido ``M`` predecimos usando **solo** los
partidos previos (jornadas anteriores en la misma temporada/liga). No hay
contaminación con el resultado de ``M`` ni con partidos posteriores.

Esto contrasta con la calibración actual (``calibrate_poisson``) que usa
las medias agregadas de toda la temporada — y por tanto sí filtran datos
del propio partido ("leakage"). El backtest da una cota superior realista
del rendimiento del modelo en producción.

Diseño:

* Iteramos los partidos por liga, en orden cronológico.
* Mantenemos un acumulador por equipo: matches jugados, GF y GA acumulados.
* En cada partido `M`, si ambos equipos llevan al menos ``MIN_HISTORY``
  partidos previos, predecimos. Si no, lo descartamos (warm-up).
* Después de predecir, **actualizamos** el acumulador con el resultado real
  para las siguientes iteraciones.

Devuelve un :class:`PoissonBacktestResult` que contiene un
:class:`~backend.services.poisson_calibration.CalibrationReport` global,
otro por liga, y metadatos (``home_advantage``, ``min_history``,
``skipped_warmup``...).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from backend.services import poisson_calibration as calib
from backend.services import poisson_model as pm


@dataclass(frozen=True)
class BacktestMatch:
    """Datos mínimos de un partido para el backtest.

    El ``commence_time`` es necesario para ordenar cronológicamente
    dentro de cada liga.
    """

    league_name: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    commence_time: datetime


@dataclass
class _TeamAcc:
    """Acumulador por equipo dentro de una liga."""

    matches: int = 0
    goals_for: int = 0
    goals_against: int = 0

    def averages(self) -> pm.TeamLeagueAverages:
        if self.matches == 0:
            return pm.TeamLeagueAverages(0, 0.0, 0.0)
        return pm.TeamLeagueAverages(
            matches_played=self.matches,
            goals_for_per_match=self.goals_for / self.matches,
            goals_against_per_match=self.goals_against / self.matches,
        )

    def update(self, gf: int, ga: int) -> None:
        self.matches += 1
        self.goals_for += gf
        self.goals_against += ga


@dataclass
class PoissonBacktestResult:
    """Resultado de un walk-forward backtest."""

    home_advantage: float
    min_history: int
    dixon_coles_rho: float = 0.0

    # Conteos
    matches_total: int = 0
    matches_evaluated: int = 0
    matches_skipped_warmup: int = 0

    global_report: calib.CalibrationReport = field(
        default_factory=calib.CalibrationReport,
    )
    per_league: dict[str, calib.CalibrationReport] = field(default_factory=dict)


# Histórico mínimo por equipo (en partidos jugados) antes de empezar a evaluar.
DEFAULT_MIN_HISTORY = 5


def run_backtest(
    matches: Iterable[BacktestMatch],
    *,
    home_advantage: float = pm.HOME_ADVANTAGE,
    min_history: int = DEFAULT_MIN_HISTORY,
    dixon_coles_rho: float = 0.0,
    log: logging.Logger | None = None,
) -> PoissonBacktestResult:
    """Ejecuta el walk-forward backtest sobre todos los partidos dados.

    :param matches: iterable de :class:`BacktestMatch`. Pueden venir
        mezcladas de distintas ligas — se agrupan internamente.
    :param home_advantage: multiplicador para λ_home (default el del modelo).
    :param min_history: partidos previos mínimos por equipo antes de
        empezar a evaluar (warm-up).
    :param dixon_coles_rho: corrección DC sobre la matriz score.
    """
    log = log or logging.getLogger(__name__)
    matches_list = list(matches)
    result = PoissonBacktestResult(
        home_advantage=home_advantage,
        min_history=min_history,
        dixon_coles_rho=dixon_coles_rho,
        matches_total=len(matches_list),
    )
    if not matches_list:
        return result

    # Agrupar por liga; cada liga es independiente (la media y los
    # acumuladores no se mezclan entre ligas).
    by_league: dict[str, list[BacktestMatch]] = {}
    for m in matches_list:
        by_league.setdefault(m.league_name, []).append(m)

    all_samples: list[calib.CalibrationSample] = []

    for league_name, league_matches in by_league.items():
        league_matches.sort(key=lambda m: m.commence_time)
        league_samples: list[calib.CalibrationSample] = []
        teams: dict[str, _TeamAcc] = {}

        for m in league_matches:
            home_acc = teams.setdefault(m.home_team, _TeamAcc())
            away_acc = teams.setdefault(m.away_team, _TeamAcc())

            if home_acc.matches < min_history or away_acc.matches < min_history:
                # Aún en warm-up: no predecimos. Pero sí actualizamos.
                home_acc.update(m.home_score, m.away_score)
                away_acc.update(m.away_score, m.home_score)
                result.matches_skipped_warmup += 1
                continue

            # Construimos las medias de la liga con TODOS los acumuladores
            # actuales (cualquier equipo con al menos un partido jugado).
            league_team_avgs = [t.averages() for t in teams.values() if t.matches > 0]
            try:
                expectation = pm.predict(
                    home=home_acc.averages(),
                    away=away_acc.averages(),
                    league_teams=league_team_avgs,
                    home_advantage=home_advantage,
                    dixon_coles_rho=dixon_coles_rho,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Predicción falló para %s vs %s: %s",
                            m.home_team, m.away_team, exc)
                home_acc.update(m.home_score, m.away_score)
                away_acc.update(m.away_score, m.home_score)
                continue

            sample = calib.CalibrationSample(
                home_team=m.home_team,
                away_team=m.away_team,
                home_score=m.home_score,
                away_score=m.away_score,
                expectation=expectation,
            )
            league_samples.append(sample)
            all_samples.append(sample)
            result.matches_evaluated += 1

            # Actualizamos los acumuladores DESPUÉS de evaluar.
            home_acc.update(m.home_score, m.away_score)
            away_acc.update(m.away_score, m.home_score)

        if league_samples:
            league_report = calib.compute_metrics(league_samples)
            league_report.reliability_home_win = calib.reliability_bins(
                [(s.expectation.p_home_win, 1 if s.outcome_1x2 == "H" else 0)
                 for s in league_samples],
            )
            league_report.reliability_over_25 = calib.reliability_bins(
                [(s.expectation.p_over_25, 1 if s.total_goals > 2 else 0)
                 for s in league_samples],
            )
            result.per_league[league_name] = league_report
            log.info("[%s] backtest: %d eval (warm-up %d), acc=%.3f brier=%.4f",
                     league_name, len(league_samples),
                     len(league_matches) - len(league_samples),
                     league_report.accuracy_1x2, league_report.brier_1x2)

    if all_samples:
        result.global_report = calib.compute_metrics(all_samples)
        result.global_report.reliability_home_win = calib.reliability_bins(
            [(s.expectation.p_home_win, 1 if s.outcome_1x2 == "H" else 0)
             for s in all_samples],
        )
        result.global_report.reliability_over_25 = calib.reliability_bins(
            [(s.expectation.p_over_25, 1 if s.total_goals > 2 else 0)
             for s in all_samples],
        )

    log.info(
        "Backtest global: %d/%d evaluados (%d warm-up). acc=%.3f brier=%.4f",
        result.matches_evaluated, result.matches_total,
        result.matches_skipped_warmup,
        result.global_report.accuracy_1x2, result.global_report.brier_1x2,
    )
    return result


__all__ = ["BacktestMatch", "PoissonBacktestResult", "run_backtest", "DEFAULT_MIN_HISTORY"]
