"""Tests del parser de stats por partido de Flashscore (``df_st_1``)."""

from __future__ import annotations

import pytest

from backend.scrapers.flashscore import (
    _parse_decimal,
    _parse_int,
    _parse_pct_with_detail,
    _parse_percent,
    parse_match_statistics,
    parse_payload,
)


# Real payload extraído del partido Chelsea 1-3 Nottingham (2026-05-04)
SAMPLE_PAYLOAD = (
    "SE÷Match¬"
    "~SF÷Top stats¬"
    "~SD÷432¬SG÷Expected goals (xG)¬SH÷2.15¬SI÷2.05¬"
    "~SD÷12¬SG÷Ball possession¬SH÷67%¬SI÷33%¬"
    "~SD÷34¬SG÷Total shots¬SH÷21¬SI÷6¬"
    "~SD÷13¬SG÷Shots on target¬SH÷5¬SI÷4¬"
    "~SD÷459¬SG÷Big chances¬SH÷2¬SI÷3¬"
    "~SD÷16¬SG÷Corner kicks¬SH÷10¬SI÷1¬"
    "~SD÷342¬SG÷Passes¬SH÷91% (597/659)¬SI÷80% (267/334)¬"
    "~SD÷23¬SG÷Yellow cards¬SH÷3¬SI÷1¬"
    "~SF÷Shots¬"
    "~SD÷499¬SG÷xG on target (xGOT)¬SH÷1.62¬SI÷2.64¬"
    "~SD÷14¬SG÷Shots off target¬SH÷8¬SI÷1¬"
    "~SD÷158¬SG÷Blocked shots¬SH÷8¬SI÷1¬"
    "~SD÷461¬SG÷Shots inside the box¬SH÷12¬SI÷6¬"
    "~SD÷463¬SG÷Shots outside the box¬SH÷9¬SI÷0¬"
    "~SF÷Defense¬"
    "~SD÷21¬SG÷Fouls¬SH÷12¬SI÷10¬"
    "~SD÷475¬SG÷Tackles¬SH÷43% (6/14)¬SI÷45% (10/22)¬"
)


class TestPrimitiveParsers:
    def test_parse_int_basic(self) -> None:
        assert _parse_int("21") == 21
        assert _parse_int("0") == 0
        assert _parse_int("-3") == -3

    def test_parse_int_strips_commas(self) -> None:
        assert _parse_int("1,234") == 1234

    def test_parse_int_invalid(self) -> None:
        assert _parse_int("") is None
        assert _parse_int(None) is None
        assert _parse_int("21%") is None
        assert _parse_int("abc") is None

    def test_parse_decimal(self) -> None:
        assert _parse_decimal("2.15") == pytest.approx(2.15)
        assert _parse_decimal("0,98") == pytest.approx(0.98)
        assert _parse_decimal(None) is None
        assert _parse_decimal("abc") is None

    def test_parse_percent(self) -> None:
        assert _parse_percent("67%") == 67
        assert _parse_percent("0%") == 0
        assert _parse_percent("100%") == 100
        assert _parse_percent("") is None
        assert _parse_percent(None) is None

    def test_parse_pct_with_detail(self) -> None:
        assert _parse_pct_with_detail("91% (597/659)") == (91, 597, 659)
        assert _parse_pct_with_detail("0% (0/3)") == (0, 0, 3)

    def test_parse_pct_with_detail_fallback(self) -> None:
        # Sin detalle: solo porcentaje
        assert _parse_pct_with_detail("67%") == (67, None, None)

    def test_parse_pct_with_detail_invalid(self) -> None:
        assert _parse_pct_with_detail(None) == (None, None, None)
        assert _parse_pct_with_detail("") == (None, None, None)


class TestParseMatchStatistics:
    def setup_method(self) -> None:
        self.records = parse_payload(SAMPLE_PAYLOAD)
        self.stats = parse_match_statistics(self.records)

    def test_returns_home_and_away(self) -> None:
        assert "home" in self.stats and "away" in self.stats

    def test_xg_decimal(self) -> None:
        assert self.stats["home"]["xg"] == pytest.approx(2.15)
        assert self.stats["away"]["xg"] == pytest.approx(2.05)

    def test_xg_on_target(self) -> None:
        assert self.stats["home"]["xg_on_target"] == pytest.approx(1.62)
        assert self.stats["away"]["xg_on_target"] == pytest.approx(2.64)

    def test_possession(self) -> None:
        assert self.stats["home"]["possession_pct"] == 67
        assert self.stats["away"]["possession_pct"] == 33

    def test_shots(self) -> None:
        assert self.stats["home"]["shots_total"] == 21
        assert self.stats["away"]["shots_total"] == 6
        assert self.stats["home"]["shots_on_target"] == 5
        assert self.stats["away"]["shots_on_target"] == 4
        assert self.stats["home"]["shots_off_target"] == 8
        assert self.stats["away"]["shots_off_target"] == 1
        assert self.stats["home"]["shots_blocked"] == 8
        assert self.stats["home"]["shots_inside_box"] == 12
        assert self.stats["home"]["shots_outside_box"] == 9

    def test_corners_yellow_fouls(self) -> None:
        assert self.stats["home"]["corners"] == 10
        assert self.stats["away"]["corners"] == 1
        assert self.stats["home"]["yellow_cards"] == 3
        assert self.stats["away"]["yellow_cards"] == 1
        assert self.stats["home"]["fouls"] == 12
        assert self.stats["away"]["fouls"] == 10

    def test_big_chances(self) -> None:
        assert self.stats["home"]["big_chances"] == 2
        assert self.stats["away"]["big_chances"] == 3

    def test_passes_with_detail(self) -> None:
        # "91% (597/659)"
        assert self.stats["home"]["passes_pct"] == 91
        assert self.stats["home"]["passes_completed"] == 597
        assert self.stats["home"]["passes_attempted"] == 659
        # "80% (267/334)"
        assert self.stats["away"]["passes_pct"] == 80
        assert self.stats["away"]["passes_completed"] == 267
        assert self.stats["away"]["passes_attempted"] == 334

    def test_tackles_with_detail(self) -> None:
        assert self.stats["home"]["tackles_pct"] == 43
        assert self.stats["home"]["tackles_completed"] == 6
        assert self.stats["home"]["tackles_attempted"] == 14
        assert self.stats["away"]["tackles_pct"] == 45

    def test_first_occurrence_wins(self) -> None:
        """Stats listadas en varias secciones (Top stats + Shots) se persisten
        una sola vez con el valor de la primera aparición."""
        # Total shots aparece en Top stats. Si Flashscore lo repitiera con
        # otro valor en otra sección, debe ganar el primero.
        records_dup = parse_payload(SAMPLE_PAYLOAD + "~SD÷34¬SG÷Total shots¬SH÷999¬SI÷999¬")
        stats = parse_match_statistics(records_dup)
        assert stats["home"]["shots_total"] == 21  # Primera ocurrencia, no 999

    def test_unknown_stats_ignored(self) -> None:
        records = parse_payload("~SD÷999¬SG÷Some unknown metric¬SH÷42¬SI÷7¬")
        stats = parse_match_statistics(records)
        assert stats == {"home": {}, "away": {}}

    def test_empty_input(self) -> None:
        assert parse_match_statistics([]) == {"home": {}, "away": {}}
