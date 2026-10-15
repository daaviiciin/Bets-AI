"""Calibración del modelo Poisson contra resultados reales.

Pipeline
--------

1.  Para cada liga top-5 con ``TeamSeasonStats`` en BD:

    a. Pide a Sofascore el ``season_id`` actual y descarga partidos finalizados
       (``/events/last/{page}``).
    b. Mapea cada equipo del feed a un equipo en BD usando ``external_id``.
    c. Construye los inputs Poisson (averages de home y away + lista de
       averages de toda la liga).

2.  Predice cada partido con el modelo y compara contra el resultado real.

3.  Imprime métricas globales, por liga, y un grid search del único
    hiperparámetro libre (``home_advantage``).

⚠️  **Sesgo de leakage**
~~~~~~~~~~~~~~~~~~~~~~~~

Las stats agregadas que tenemos en ``TeamSeasonStats`` son las **finales** de
la temporada hasta el momento del scrape. Eso significa que cuando predecimos
un partido pasado, el resultado de ese partido **ya está incluido** en las
stats de los dos equipos. La calibración resultante es por tanto un
**upper bound** optimista: el modelo no puede ser más malo que esto, y en
producción (con stats hasta justo antes del partido) será algo peor.

Para una calibración rigurosa deberíamos:

* Bajar stats por partido (Flashscore ``df_st_1``) → reconstruir form
  rolling, o
* Calibrar contra una temporada anterior cuyos partidos NO contribuyeron a
  las stats que usamos.

Lo dejamos como mejora futura. Hoy la métrica sirve para detectar bugs
gruesos y para comparar valores de ``home_advantage``.

Uso
~~~

::

    python -m backend.tools.calibrate_poisson
    python -m backend.tools.calibrate_poisson --max-pages 3 --liga soccer_spain_la_liga
    python -m backend.tools.calibrate_poisson --grid 1.0 1.05 1.1 1.15 1.2
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Iterable

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import (
    CalibrationReport as CalibrationReportRow,
    League,
    Season,
    Team,
    TeamSeasonStats,
)
from backend.scrapers.sofascore import (
    TOURNAMENTS,
    fetch_finished_events,
    fetch_seasons,
)
from backend.services import poisson_model as pm
from backend.services.poisson_calibration import (
    CalibrationReport,
    CalibrationSample,
    compute_metrics,
    grid_search_combined,
    grid_search_home_advantage,
)

DEFAULT_GRID = (1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30)
DEFAULT_RHO_GRID = (-0.15, -0.10, -0.05, 0.0, 0.05, 0.10)
DEFAULT_MAX_PAGES = 5


# ---------------------------------------------------------------------------
# Estructuras intermedias
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchPredictionInput:
    """Datos necesarios para predecir un partido y comparar con su resultado."""

    league_name: str
    home_team_name: str
    away_team_name: str
    home_score: int
    away_score: int
    home_avg: pm.TeamLeagueAverages
    away_avg: pm.TeamLeagueAverages
    league_avgs: tuple[pm.TeamLeagueAverages, ...]


@dataclass
class LeagueDataset:
    """Conjunto de partidos cargados para una liga."""

    league_name: str
    sport_key: str
    inputs: list[MatchPredictionInput]
    skipped_unmapped: int = 0
    skipped_no_stats: int = 0


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------


def _to_averages(stats: TeamSeasonStats) -> pm.TeamLeagueAverages | None:
    if not stats.matches_played or stats.matches_played <= 0:
        return None
    if stats.goals_for is None or stats.goals_against is None:
        return None
    return pm.TeamLeagueAverages(
        matches_played=stats.matches_played,
        goals_for_per_match=stats.goals_for / stats.matches_played,
        goals_against_per_match=stats.goals_against / stats.matches_played,
    )


def _is_finished(event: dict) -> bool:
    status = event.get("status") or {}
    return status.get("type") == "finished" or status.get("code") == 100


def _team_ext_id(event: dict, side: str) -> str | None:
    team = event.get(f"{side}Team") or {}
    tid = team.get("id")
    return str(tid) if tid is not None else None


def _scores(event: dict) -> tuple[int, int] | None:
    home = (event.get("homeScore") or {}).get("current")
    away = (event.get("awayScore") or {}).get("current")
    if home is None or away is None:
        return None
    try:
        return int(home), int(away)
    except (TypeError, ValueError):
        return None


def load_league_dataset(
    session: Session,
    *,
    sport_key: str,
    league_name: str,
    tournament_id: int,
    max_pages: int,
    log: logging.Logger,
) -> LeagueDataset | None:
    """Construye :class:`MatchPredictionInput`'s para una liga."""
    league = session.execute(
        select(League).where(League.sport_key == sport_key)
    ).scalar_one_or_none()
    if league is None:
        log.warning("[%s] no hay League en BD; saltando", league_name)
        return None

    season = session.execute(
        select(Season)
        .where(Season.league_id == league.id)
        .order_by(Season.year_start.desc())
    ).scalars().first()
    if season is None:
        log.warning("[%s] sin Season en BD; saltando", league_name)
        return None

    team_stats_rows = session.execute(
        select(TeamSeasonStats).where(TeamSeasonStats.season_id == season.id)
    ).scalars().all()
    if not team_stats_rows:
        log.warning("[%s] sin TeamSeasonStats en BD; saltando", league_name)
        return None

    league_averages: list[pm.TeamLeagueAverages] = []
    averages_by_team_id: dict[int, pm.TeamLeagueAverages] = {}
    for ts in team_stats_rows:
        avg = _to_averages(ts)
        if avg is None:
            continue
        averages_by_team_id[ts.team_id] = avg
        league_averages.append(avg)
    if not league_averages:
        log.warning("[%s] TeamSeasonStats con datos vacíos; saltando", league_name)
        return None

    teams_by_ext_id: dict[str, Team] = {}
    teams = session.execute(
        select(Team).where(Team.external_source == "sofascore")
    ).scalars().all()
    for t in teams:
        if t.external_id:
            teams_by_ext_id[t.external_id] = t

    log.info(
        "[%s] season=%s  teams_with_avg=%d  teams_with_extid=%d",
        league_name, season.name, len(averages_by_team_id), len(teams_by_ext_id),
    )

    log.info("[%s] descargando partidos pasados de Sofascore...", league_name)
    sf_seasons = fetch_seasons(tournament_id)
    if not sf_seasons:
        log.warning("[%s] sin temporadas en Sofascore", league_name)
        return None
    sofa_season_id = sf_seasons[0]["id"]
    events = fetch_finished_events(tournament_id, sofa_season_id, max_pages=max_pages)
    log.info("[%s] eventos descargados: %d", league_name, len(events))

    dataset = LeagueDataset(league_name=league_name, sport_key=sport_key, inputs=[])

    for event in events:
        if not _is_finished(event):
            continue

        home_ext = _team_ext_id(event, "home")
        away_ext = _team_ext_id(event, "away")
        if not home_ext or not away_ext:
            continue

        home_team = teams_by_ext_id.get(home_ext)
        away_team = teams_by_ext_id.get(away_ext)
        if home_team is None or away_team is None:
            dataset.skipped_unmapped += 1
            continue

        home_avg = averages_by_team_id.get(home_team.id)
        away_avg = averages_by_team_id.get(away_team.id)
        if home_avg is None or away_avg is None:
            dataset.skipped_no_stats += 1
            continue

        scores = _scores(event)
        if scores is None:
            continue
        hs, as_ = scores

        dataset.inputs.append(
            MatchPredictionInput(
                league_name=league_name,
                home_team_name=home_team.name,
                away_team_name=away_team.name,
                home_score=hs,
                away_score=as_,
                home_avg=home_avg,
                away_avg=away_avg,
                league_avgs=tuple(league_averages),
            )
        )

    log.info(
        "[%s] usables=%d  skipped_unmapped=%d  skipped_no_stats=%d",
        league_name, len(dataset.inputs),
        dataset.skipped_unmapped, dataset.skipped_no_stats,
    )
    return dataset


# ---------------------------------------------------------------------------
# Predicción y métricas
# ---------------------------------------------------------------------------


def predict_samples(
    inputs: Iterable[MatchPredictionInput],
    *,
    home_advantage: float,
    dixon_coles_rho: float = 0.0,
) -> list[CalibrationSample]:
    samples: list[CalibrationSample] = []
    for inp in inputs:
        expectation = pm.predict(
            inp.home_avg, inp.away_avg, list(inp.league_avgs),
            home_advantage=home_advantage,
            dixon_coles_rho=dixon_coles_rho,
        )
        samples.append(
            CalibrationSample(
                home_team=inp.home_team_name,
                away_team=inp.away_team_name,
                home_score=inp.home_score,
                away_score=inp.away_score,
                expectation=expectation,
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(title: str, report: CalibrationReport) -> None:
    print(f"  {title:30s}  n={report.n_samples}")
    print(
        "    1X2:    "
        f"acc={report.accuracy_1x2*100:5.1f}%  "
        f"brier={report.brier_1x2:.4f}  "
        f"log_loss={report.log_loss_1x2:.4f}"
    )
    print(
        "    O/U2.5: "
        f"brier={report.brier_over_25:.4f}  "
        f"log_loss={report.log_loss_over_25:.4f}  "
        f"observed_over={report.observed_over_25_rate*100:5.1f}%"
    )
    print(
        "    BTTS:   "
        f"brier={report.brier_btts:.4f}  "
        f"log_loss={report.log_loss_btts:.4f}  "
        f"observed_yes={report.observed_btts_rate*100:5.1f}%"
    )
    print(
        "    Goles:  "
        f"MAE_total={report.mae_total_goals:.3f}  "
        f"MAE_home={report.mae_home_goals:.3f}  "
        f"MAE_away={report.mae_away_goals:.3f}"
    )
    print(
        "    Real:   "
        f"H={report.observed_home_rate*100:4.1f}%  "
        f"D={report.observed_draw_rate*100:4.1f}%  "
        f"A={report.observed_away_rate*100:4.1f}%"
    )


def _print_reliability(title: str, bins) -> None:
    if not bins:
        return
    print(f"\n  {title}  (predicha vs observada)")
    print("    bin           n    pred    obs")
    print("    " + "-" * 30)
    for b in bins:
        bar = "█" * round(b.observed_frequency * 20)
        print(
            f"    [{b.lower:.1f}-{b.upper:.1f})  "
            f"{b.count:4d}  {b.mean_predicted:.3f}  {b.observed_frequency:.3f}  {bar}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibra el modelo Poisson contra resultados reales (con leakage).",
    )
    parser.add_argument(
        "--liga", choices=list(TOURNAMENTS.keys()), default=None,
        help="Limita a una sola liga (sport_key).",
    )
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
        help="Páginas de partidos pasados a descargar de Sofascore (~30 por página).",
    )
    parser.add_argument(
        "--grid", type=float, nargs="+", default=DEFAULT_GRID,
        help="Valores de home_advantage a probar.",
    )
    parser.add_argument(
        "--rho-grid", type=float, nargs="+", default=DEFAULT_RHO_GRID,
        help="Valores de dixon_coles_rho a probar (combinados con --grid).",
    )
    parser.add_argument(
        "--rho", type=float, default=None,
        help=("Si se especifica, fuerza ese rho para las métricas globales/por liga "
              "y desactiva el grid combinado (sólo se usa --grid sobre HA)."),
    )
    parser.add_argument(
        "--no-grid", action="store_true",
        help="Salta cualquier grid search (solo evalúa con defaults o --rho).",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Persistir el reporte en la BD (tabla calibration_reports).",
    )
    parser.add_argument(
        "--note", default=None,
        help="Texto descriptivo del run (visible en el dashboard).",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("calibrate_poisson")

    leagues = (
        {args.liga: TOURNAMENTS[args.liga]} if args.liga else TOURNAMENTS
    )

    SF = get_session_factory()
    all_inputs: list[MatchPredictionInput] = []
    per_league_inputs: dict[str, list[MatchPredictionInput]] = {}
    with SF() as session:
        for sport_key, (league_name, tournament_id) in leagues.items():
            ds = load_league_dataset(
                session,
                sport_key=sport_key,
                league_name=league_name,
                tournament_id=tournament_id,
                max_pages=args.max_pages,
                log=log,
            )
            if ds is None:
                continue
            per_league_inputs[league_name] = ds.inputs
            all_inputs.extend(ds.inputs)

    if not all_inputs:
        print("No se han podido cargar partidos para evaluar.")
        return 1

    # rho elegido para las métricas "default" (lo que se imprime y persiste).
    rho_default: float = float(args.rho) if args.rho is not None else 0.0

    print()
    print("=" * 80)
    print(f"CALIBRACIÓN POISSON  ·  total partidos = {len(all_inputs)}  "
          f"·  HA={pm.HOME_ADVANTAGE}  ·  ρ={rho_default}")
    print("⚠️  Sesgo de leakage: stats incluyen el resultado del propio partido.")
    print("=" * 80)

    # --- Métricas con default ---
    samples_default = predict_samples(
        all_inputs,
        home_advantage=pm.HOME_ADVANTAGE,
        dixon_coles_rho=rho_default,
    )
    report_global = compute_metrics(samples_default)
    _print_report("GLOBAL  (todas las ligas)", report_global)

    print()
    print("Por liga:")
    for league_name, inputs in per_league_inputs.items():
        if not inputs:
            continue
        samples_lg = predict_samples(
            inputs,
            home_advantage=pm.HOME_ADVANTAGE,
            dixon_coles_rho=rho_default,
        )
        report_lg = compute_metrics(samples_lg)
        _print_report(league_name, report_lg)

    _print_reliability("Reliability HOME WIN", report_global.reliability_home_win)
    _print_reliability("Reliability OVER 2.5", report_global.reliability_over_25)

    # --- Grid search ---
    grid_search_results: list[dict[str, float]] = []
    if not args.no_grid:
        if args.rho is not None:
            # Modo simple: barremos solo HA con rho fijado por --rho.
            print()
            print("=" * 80)
            print(f"Grid search · home_advantage  (ρ fijo = {args.rho})")
            print("=" * 80)

            def factory_ha(ha: float) -> list[CalibrationSample]:
                return predict_samples(
                    all_inputs, home_advantage=ha, dixon_coles_rho=float(args.rho),
                )

            results = grid_search_home_advantage(factory_ha, values=tuple(args.grid))
            # Inyectamos el rho fijo para reporting unificado.
            from dataclasses import replace as _replace
            results = [_replace(r, dixon_coles_rho=float(args.rho)) for r in results]
        else:
            # Grid combinado HA × ρ.
            print()
            print("=" * 80)
            print("Grid search · home_advantage × dixon_coles_rho")
            print("=" * 80)

            def factory_combo(ha: float, rho: float) -> list[CalibrationSample]:
                return predict_samples(
                    all_inputs, home_advantage=ha, dixon_coles_rho=rho,
                )

            results = grid_search_combined(
                factory_combo,
                home_advantage_values=tuple(args.grid),
                dixon_coles_values=tuple(args.rho_grid),
            )

        print(f"  {'HA':>5}  {'rho':>6}  {'acc_1x2':>8}  {'brier_1x2':>10}  "
              f"{'brier_o25':>10}  {'brier_btts':>10}  {'loss':>8}")
        print("  " + "-" * 70)
        best = min(results, key=lambda r: r.loss)
        for r in results:
            mark = "  ←  best" if r is best else ""
            print(
                f"  {r.home_advantage:5.2f}  "
                f"{r.dixon_coles_rho:>+6.3f}  "
                f"{r.report.accuracy_1x2*100:7.1f}%  "
                f"{r.report.brier_1x2:10.4f}  "
                f"{r.report.brier_over_25:10.4f}  "
                f"{r.report.brier_btts:10.4f}  "
                f"{r.loss:8.4f}{mark}"
            )
            grid_search_results.append({
                "home_advantage": r.home_advantage,
                "dixon_coles_rho": r.dixon_coles_rho,
                "accuracy_1x2": r.report.accuracy_1x2,
                "brier_1x2": r.report.brier_1x2,
                "brier_over_25": r.report.brier_over_25,
                "brier_btts": r.report.brier_btts,
                "loss": r.loss,
            })

    # --- Persistir si se pidió ---
    if args.save:
        per_league_payload: list[dict] = []
        for league_name, inputs in per_league_inputs.items():
            if not inputs:
                continue
            samples_lg = predict_samples(
                inputs,
                home_advantage=pm.HOME_ADVANTAGE,
                dixon_coles_rho=rho_default,
            )
            report_lg = compute_metrics(samples_lg)
            per_league_payload.append({
                "league_name": league_name,
                **report_lg.as_dict(),
            })

        with SF() as session:
            row = CalibrationReportRow(
                home_advantage=Decimal(str(pm.HOME_ADVANTAGE)),
                dixon_coles_rho=Decimal(str(rho_default)),
                n_samples=report_global.n_samples,
                note=args.note,
                brier_1x2=Decimal(str(report_global.brier_1x2)),
                log_loss_1x2=Decimal(str(report_global.log_loss_1x2)),
                accuracy_1x2=Decimal(str(report_global.accuracy_1x2)),
                brier_btts=Decimal(str(report_global.brier_btts)),
                log_loss_btts=Decimal(str(report_global.log_loss_btts)),
                brier_over_25=Decimal(str(report_global.brier_over_25)),
                log_loss_over_25=Decimal(str(report_global.log_loss_over_25)),
                mae_total_goals=Decimal(str(report_global.mae_total_goals)),
                mae_home_goals=Decimal(str(report_global.mae_home_goals)),
                mae_away_goals=Decimal(str(report_global.mae_away_goals)),
                observed_home_rate=Decimal(str(report_global.observed_home_rate)),
                observed_draw_rate=Decimal(str(report_global.observed_draw_rate)),
                observed_away_rate=Decimal(str(report_global.observed_away_rate)),
                observed_over_25_rate=Decimal(str(report_global.observed_over_25_rate)),
                observed_btts_rate=Decimal(str(report_global.observed_btts_rate)),
                per_league=per_league_payload,
                reliability_home_win=[_bin_to_dict(b) for b in report_global.reliability_home_win],
                reliability_over_25=[_bin_to_dict(b) for b in report_global.reliability_over_25],
                grid_search=grid_search_results,
            )
            session.add(row)
            session.commit()
            print()
            print(f"✓ Reporte guardado en BD (id={row.id}, ts={row.created_at})")

    return 0


def _bin_to_dict(b) -> dict[str, float]:
    return {
        "lower": b.lower,
        "upper": b.upper,
        "count": b.count,
        "mean_predicted": b.mean_predicted,
        "observed_frequency": b.observed_frequency,
    }


if __name__ == "__main__":
    sys.exit(main())
