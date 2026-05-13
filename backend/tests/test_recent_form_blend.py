"""Tests puros de la mezcla temporada + forma reciente (goles por rol)."""

from backend.services import poisson_model as pm
from backend.services.value_detector import _blend_season_with_venue_rates


def test_blend_season_and_recent() -> None:
    season = pm.TeamLeagueAverages(matches_played=20, goals_for_per_match=1.0, goals_against_per_match=1.0)
    recent = (3.0, 0.5, 5)
    b = _blend_season_with_venue_rates(season, recent, weight=0.5)
    assert b.matches_played >= 20
    assert abs(b.goals_for_per_match - 2.0) < 1e-9
    assert abs(b.goals_against_per_match - 0.75) < 1e-9


def test_blend_skips_when_no_recent() -> None:
    season = pm.TeamLeagueAverages(matches_played=10, goals_for_per_match=1.2, goals_against_per_match=0.9)
    b = _blend_season_with_venue_rates(season, None, weight=0.5)
    assert b == season
