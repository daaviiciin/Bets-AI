"""CLI: backtest walk-forward del modelo Poisson sobre la temporada 2024-25.

Descarga los partidos finalizados de la temporada anterior en cada una de
las 5 grandes ligas (Sofascore unofficial API) y corre el backtest sin
leakage. Imprime métricas globales y por liga, opcionalmente compara con
la última calibración guardada y persiste el resultado.

Uso::

    python -m backend.tools.backtest_poisson
    python -m backend.tools.backtest_poisson --save
    python -m backend.tools.backtest_poisson --season-name "24/25"
    python -m backend.tools.backtest_poisson --liga soccer_spain_la_liga
    python -m backend.tools.backtest_poisson --min-history 8 --home-advantage 1.30

Notas:

* Por defecto buscamos la **segunda** temporada que devuelve Sofascore (la
  actual está en ``seasons[0]``, la pasada en ``seasons[1]``). Con
  ``--season-name`` puedes forzar otra (matching por substring).
* Es 100% gratis: solo consume Sofascore, no Odds API.
* Una temporada completa por liga ≈ 380 partidos. 15 páginas × 30 ≈ 450
  cubre con margen.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.models import CalibrationReport
from backend.scrapers import sofascore as ss
from backend.services.poisson_backtest import (
    BacktestMatch,
    PoissonBacktestResult,
    run_backtest,
)
from backend.services.poisson_model import HOME_ADVANTAGE


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _common_season_suffix(labels: list[str]) -> str | None:
    """Si todos los labels comparten el mismo sufijo (ej. ``"24/25"``), lo devuelve.

    Heurística simple: extrae el último token tras el último espacio en cada
    label y, si todos coinciden, devuelve ese token. Robustece el slug del
    note al persistir un backtest sobre varias ligas con labels distintos
    (``"LaLiga 24/25"``, ``"Premier League 24/25"``...).
    """
    suffixes = {lab.rsplit(" ", 1)[-1] for lab in labels if lab}
    if len(suffixes) == 1:
        return next(iter(suffixes))
    return None


def _pick_season(seasons: list[dict], season_name: str | None) -> dict | None:
    """Selecciona la temporada objetivo. Default: la segunda (la pasada)."""
    if not seasons:
        return None
    if season_name:
        target = season_name.lower().replace("-", "/")
        for s in seasons:
            haystack = (str(s.get("name") or "") + " " + str(s.get("year") or "")).lower()
            if target in haystack:
                return s
        return None
    # Default: la pasada (después de la actual)
    return seasons[1] if len(seasons) > 1 else seasons[0]


def _events_to_matches(events: list[dict], league_name: str) -> list[BacktestMatch]:
    out: list[BacktestMatch] = []
    for ev in events:
        # Solo finalizados
        status = (ev.get("status") or {}).get("type")
        if status not in {"finished", "ended"}:
            continue
        home_team = ((ev.get("homeTeam") or {}).get("name") or "").strip()
        away_team = ((ev.get("awayTeam") or {}).get("name") or "").strip()
        if not home_team or not away_team:
            continue
        hs = ((ev.get("homeScore") or {}).get("current"))
        as_ = ((ev.get("awayScore") or {}).get("current"))
        if hs is None or as_ is None:
            continue
        ts = ev.get("startTimestamp")
        if not isinstance(ts, (int, float)):
            continue
        out.append(BacktestMatch(
            league_name=league_name,
            home_team=home_team,
            away_team=away_team,
            home_score=int(hs),
            away_score=int(as_),
            commence_time=datetime.fromtimestamp(int(ts), tz=timezone.utc),
        ))
    return out


def _print_report(name: str, n: int, report) -> None:  # noqa: ANN001
    print(f"\n  {name}  (n={n})")
    print(f"    accuracy_1x2 = {report.accuracy_1x2:.3f}    "
          f"brier_1x2 = {report.brier_1x2:.4f}    "
          f"log_loss_1x2 = {report.log_loss_1x2:.4f}")
    print(f"    brier_over25 = {report.brier_over_25:.4f}   "
          f"brier_btts = {report.brier_btts:.4f}   "
          f"mae_total_goals = {report.mae_total_goals:.3f}")
    print(f"    obs(H/D/A) = {report.observed_home_rate:.2f} / "
          f"{report.observed_draw_rate:.2f} / "
          f"{report.observed_away_rate:.2f}    "
          f"obs(O2.5/BTTS) = {report.observed_over_25_rate:.2f} / "
          f"{report.observed_btts_rate:.2f}")


def _persist_report(
    result: PoissonBacktestResult, *, note: str,
) -> int:
    """Guarda el resultado del backtest como un CalibrationReport en BD."""
    SessionLocal = get_session_factory()
    g = result.global_report
    per_league_json = [
        {
            "league_name": k,
            "n_samples": v.n_samples,
            "accuracy_1x2": v.accuracy_1x2,
            "brier_1x2": v.brier_1x2,
            "log_loss_1x2": v.log_loss_1x2,
            "brier_over_25": v.brier_over_25,
            "brier_btts": v.brier_btts,
            "mae_total_goals": v.mae_total_goals,
            "observed_home_rate": v.observed_home_rate,
            "observed_draw_rate": v.observed_draw_rate,
            "observed_away_rate": v.observed_away_rate,
            "observed_over_25_rate": v.observed_over_25_rate,
            "observed_btts_rate": v.observed_btts_rate,
        }
        for k, v in result.per_league.items()
    ]
    reliability_home_win = [
        {"lower": b.lower, "upper": b.upper, "count": b.count,
         "mean_predicted": b.mean_predicted, "observed_frequency": b.observed_frequency}
        for b in g.reliability_home_win
    ]
    reliability_over_25 = [
        {"lower": b.lower, "upper": b.upper, "count": b.count,
         "mean_predicted": b.mean_predicted, "observed_frequency": b.observed_frequency}
        for b in g.reliability_over_25
    ]

    with SessionLocal() as session:
        row = CalibrationReport(
            home_advantage=Decimal(str(result.home_advantage)),
            dixon_coles_rho=Decimal(str(result.dixon_coles_rho)),
            n_samples=g.n_samples,
            note=note,
            brier_1x2=Decimal(f"{g.brier_1x2:.6f}"),
            log_loss_1x2=Decimal(f"{g.log_loss_1x2:.6f}"),
            accuracy_1x2=Decimal(f"{g.accuracy_1x2:.4f}"),
            brier_over_25=Decimal(f"{g.brier_over_25:.6f}"),
            log_loss_over_25=Decimal(f"{g.log_loss_over_25:.6f}"),
            brier_btts=Decimal(f"{g.brier_btts:.6f}"),
            log_loss_btts=Decimal(f"{g.log_loss_btts:.6f}"),
            mae_total_goals=Decimal(f"{g.mae_total_goals:.4f}"),
            mae_home_goals=Decimal(f"{g.mae_home_goals:.4f}"),
            mae_away_goals=Decimal(f"{g.mae_away_goals:.4f}"),
            observed_home_rate=Decimal(f"{g.observed_home_rate:.4f}"),
            observed_draw_rate=Decimal(f"{g.observed_draw_rate:.4f}"),
            observed_away_rate=Decimal(f"{g.observed_away_rate:.4f}"),
            observed_over_25_rate=Decimal(f"{g.observed_over_25_rate:.4f}"),
            observed_btts_rate=Decimal(f"{g.observed_btts_rate:.4f}"),
            per_league=per_league_json,
            reliability_home_win=reliability_home_win,
            reliability_over_25=reliability_over_25,
            grid_search=[],
        )
        session.add(row)
        session.commit()
        return row.id


def _print_compare_with_db(result: PoissonBacktestResult) -> None:
    """Compara la calibración honesta (backtest) con la última de BD (con leakage)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        row = session.scalars(
            select(CalibrationReport).order_by(CalibrationReport.created_at.desc()).limit(1)
        ).first()
    if row is None:
        return
    db_brier = float(row.brier_1x2)
    bt_brier = result.global_report.brier_1x2
    db_acc = float(row.accuracy_1x2)
    bt_acc = result.global_report.accuracy_1x2
    print("\nComparación con la última calibración almacenada (puede tener leakage):")
    print(f"  {'metric':22s} {'BD (con leakage)':>20s} {'Backtest (honesto)':>22s} {'Δ':>10s}")
    print(f"  {'-' * 78}")
    print(f"  {'accuracy_1x2':22s} {db_acc:>20.3f} {bt_acc:>22.3f} {bt_acc - db_acc:>+10.3f}")
    print(f"  {'brier_1x2 (↓ mejor)':22s} {db_brier:>20.4f} {bt_brier:>22.4f} {bt_brier - db_brier:>+10.4f}")
    print(f"  {'brier_over25 (↓)':22s} {float(row.brier_over_25):>20.4f} "
          f"{result.global_report.brier_over_25:>22.4f} "
          f"{result.global_report.brier_over_25 - float(row.brier_over_25):>+10.4f}")
    print(f"  {'mae_total_goals (↓)':22s} {float(row.mae_total_goals):>20.3f} "
          f"{result.global_report.mae_total_goals:>22.3f} "
          f"{result.global_report.mae_total_goals - float(row.mae_total_goals):>+10.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest del Poisson sobre la temporada anterior.",
    )
    parser.add_argument("--liga", choices=list(ss.TOURNAMENTS.keys()), default=None,
                        help="Una sola liga. Default: todas.")
    parser.add_argument("--season-name", default=None,
                        help="Substring del nombre de la temporada (ej. '24/25').")
    parser.add_argument("--max-pages", type=int, default=15,
                        help="Páginas de Sofascore por liga (default 15 ≈ 450 partidos).")
    parser.add_argument("--min-history", type=int, default=5,
                        help="Partidos previos por equipo antes de evaluar (default 5).")
    parser.add_argument("--home-advantage", type=float, default=HOME_ADVANTAGE,
                        help=f"Multiplicador home (default {HOME_ADVANTAGE}).")
    parser.add_argument("--rho", type=float, default=0.0,
                        help="Corrección Dixon-Coles (default 0.0 = Poisson independiente).")
    parser.add_argument("--save", action="store_true",
                        help="Persiste el resultado como CalibrationReport con note 'backtest_<season>'.")
    parser.add_argument("--note", default=None,
                        help="Nota custom (default 'backtest_<season-name>').")
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger("backtest")

    leagues = (
        {args.liga: ss.TOURNAMENTS[args.liga]} if args.liga else dict(ss.TOURNAMENTS)
    )

    all_matches: list[BacktestMatch] = []
    season_label = args.season_name or "—"
    season_labels: list[str] = []
    for sport_key, (league_name, tid) in leagues.items():
        log.info("[%s] descargando temporadas...", league_name)
        try:
            seasons = ss.fetch_seasons(tid)
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] error al descargar temporadas: %s", league_name, exc)
            continue

        season = _pick_season(seasons, args.season_name)
        if season is None:
            log.warning("[%s] no se encontró la temporada solicitada", league_name)
            continue
        this_label = str(season.get("name") or season.get("year") or "")
        season_labels.append(this_label)
        season_label = this_label or season_label
        log.info("[%s] temporada elegida: %s (id=%s)",
                 league_name, this_label, season.get("id"))

        try:
            events = ss.fetch_finished_events(
                tid, season["id"], max_pages=args.max_pages,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] error al descargar eventos: %s", league_name, exc)
            continue

        matches = _events_to_matches(events, league_name)
        log.info("[%s] %d partidos finalizados convertidos", league_name, len(matches))
        all_matches.extend(matches)

    if not all_matches:
        log.error("No hay partidos para backtest. Saliendo.")
        return 1

    log.info("Total de partidos para backtest: %d", len(all_matches))
    log.info("Ejecutando walk-forward (HA=%.3f, ρ=%.3f, min_history=%d)...",
             args.home_advantage, args.rho, args.min_history)
    result = run_backtest(
        all_matches,
        home_advantage=args.home_advantage,
        dixon_coles_rho=args.rho,
        min_history=args.min_history,
        log=log,
    )

    print("\n" + "=" * 90)
    print(f"BACKTEST POISSON SIN LEAKAGE  (temporada: {season_label})")
    print("=" * 90)
    print(f"  Partidos totales       : {result.matches_total}")
    print(f"  Evaluados              : {result.matches_evaluated}")
    print(f"  Saltados (warm-up)     : {result.matches_skipped_warmup}")
    print(f"  home_advantage         : {result.home_advantage:.3f}")
    print(f"  dixon_coles_rho        : {result.dixon_coles_rho:+.3f}")
    print(f"  min_history            : {result.min_history}")

    _print_report("GLOBAL", result.global_report.n_samples, result.global_report)

    if result.per_league:
        print("\n  POR LIGA:")
        for name, rep in sorted(result.per_league.items()):
            _print_report(name, rep.n_samples, rep)

    _print_compare_with_db(result)

    if args.save:
        # Extrae el sufijo común "24/25" o similar de los nombres de temporada
        # para construir un slug estable. Si no se puede, cae al label por liga.
        common = _common_season_suffix(season_labels) or season_label
        note = args.note or (
            f"backtest_{common}".replace("/", "_").replace(" ", "_")
        )
        report_id = _persist_report(result, note=note)
        print(f"\nGuardado como CalibrationReport id={report_id} note='{note}'")

    print("=" * 90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
