"""Proyecciones de baloncesto reutilizables (modelo gaussiano MVP).

Usa ``TeamSeasonStats.bb_points_*`` con fallback a ``goals_*`` (legado) y
sigmas por liga (``League.bb_sd_*``) o defaults NBA-like.
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy import stats as scipy_stats

from backend.models.league import League
from backend.models.team_stats import TeamSeasonStats


@dataclass(frozen=True)
class BasketballProjection:
    exp_home_pts: float
    exp_away_pts: float
    sd_margin: float
    sd_team_points: float
    sd_game_total: float

    @property
    def exp_game_total(self) -> float:
        return self.exp_home_pts + self.exp_away_pts


def league_basketball_sigmas(league: League) -> tuple[float, float, float]:
    return (
        float(league.bb_sd_margin) if league.bb_sd_margin is not None else 11.0,
        float(league.bb_sd_team_points) if league.bb_sd_team_points is not None else 11.0,
        float(league.bb_sd_game_total) if league.bb_sd_game_total is not None else 15.0,
    )


def bb_points_totals(stats: TeamSeasonStats) -> tuple[int | None, int | None]:
    """Totales temporada: usa columnas bb_* si existen."""
    pf = stats.bb_points_for
    pa = stats.bb_points_against
    if pf is not None and pa is not None:
        return pf, pa
    if stats.goals_for is not None and stats.goals_against is not None:
        return stats.goals_for, stats.goals_against
    return None, None


def bb_points_per_game(stats: TeamSeasonStats) -> tuple[float | None, float | None]:
    mp = stats.matches_played or 0
    if mp <= 0:
        return None, None
    pf, pa = bb_points_totals(stats)
    if pf is None or pa is None:
        return None, None
    return pf / mp, pa / mp


def project_match(
    home_stats: TeamSeasonStats,
    away_stats: TeamSeasonStats,
    league: League,
    *,
    home_court_bonus: float = 2.75,
) -> BasketballProjection | None:
    h_pf, h_pa = bb_points_per_game(home_stats)
    a_pf, a_pa = bb_points_per_game(away_stats)
    if h_pf is None or h_pa is None or a_pf is None or a_pa is None:
        return None
    exp_home = max(70.0, (h_pf + a_pa) / 2.0 + home_court_bonus / 2.0)
    exp_away = max(70.0, (a_pf + h_pa) / 2.0 - home_court_bonus / 2.0)
    sm, st, sg = league_basketball_sigmas(league)
    return BasketballProjection(
        exp_home_pts=exp_home,
        exp_away_pts=exp_away,
        sd_margin=sm,
        sd_team_points=st,
        sd_game_total=sg,
    )


def norm_cdf(x: float) -> float:
    return float(scipy_stats.norm.cdf(x))


def p_team_total_over(proj: BasketballProjection, *, side: str, line: float) -> float:
    mu = proj.exp_home_pts if side == "home" else proj.exp_away_pts
    return 1.0 - norm_cdf((line - mu) / proj.sd_team_points)


def p_game_total_over(proj: BasketballProjection, line: float) -> float:
    return 1.0 - norm_cdf((line - proj.exp_game_total) / proj.sd_game_total)


def p_home_win(proj: BasketballProjection) -> float:
    spread_mu = proj.exp_home_pts - proj.exp_away_pts
    return norm_cdf(spread_mu / proj.sd_margin)
