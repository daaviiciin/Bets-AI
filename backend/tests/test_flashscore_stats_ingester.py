from backend.services.flashscore_stats_ingester import (
    _matches_with_complete_stats,
    _orient_stats_to_database,
    _score_match_orientation,
)


def test_only_matches_with_both_sides_are_complete() -> None:
    rows = [
        (1, True),
        (1, False),
        (2, True),
        (3, False),
        (4, True),
        (4, False),
        (4, True),
    ]

    assert _matches_with_complete_stats(rows) == {1, 4}


def test_reversed_fixture_sides_are_reoriented_before_persisting() -> None:
    parsed = {
        "home": {"shots_on_target": 3},
        "away": {"shots_on_target": 8},
    }
    summary_home = {"yellow_cards_1h": 1}
    summary_away = {"yellow_cards_2h": 2}

    aligned, aligned_home, aligned_away = _orient_stats_to_database(
        parsed,
        summary_home,
        summary_away,
        reversed_sides=True,
    )

    assert aligned["home"]["shots_on_target"] == 8
    assert aligned["away"]["shots_on_target"] == 3
    assert aligned_home == summary_away
    assert aligned_away == summary_home


def test_score_fallback_requires_exact_score_and_close_kickoff() -> None:
    assert _score_match_orientation(7, 2, 7, 2, delta_minutes=0) is False
    assert _score_match_orientation(7, 2, 2, 7, delta_minutes=0) is True
    assert _score_match_orientation(7, 2, 1, 2, delta_minutes=0) is None
    assert _score_match_orientation(7, 2, 7, 2, delta_minutes=16) is None