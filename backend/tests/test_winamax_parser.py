"""Tests del parser de Winamax (PRELOADED_STATE).

Fixtures sintéticos basados en respuestas reales observadas.
Validamos:
* extracción de balanced JSON tolerando llaves en strings,
* parseo de partidos de fútbol,
* parseo de mercados y filtrado por betType,
* extracción de player props con líneas, jugador y sentido over/under.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.scrapers.winamax import (
    MARKET_TYPES,
    PLAYER_PROP_MARKETS,
    WinamaxPlayerProp,
    _extract_balanced_json,
    parse_football_matches,
    parse_match_bets,
    parse_player_props,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


SAMPLE_STATE: dict = {
    "matches": {
        "69339436": {
            "matchId": 69339436,
            "title": "Arsenal - Atlético Madrid",
            "competitor1Name": "Arsenal",
            "competitor2Name": "Atlético Madrid",
            "competitor1Id": 1644,
            "competitor2Id": 1645,
            "tournamentId": 2,
            "matchStart": 1764000000,  # 24-nov-2025 17:20 UTC aprox
            "moreBets": 565,
            "sportId": 1,
        },
        # Un basket que NO debe aparecer
        "70505000": {
            "matchId": 70505000,
            "title": "Detroit Pistons - Cavaliers",
            "competitor1Name": "Detroit",
            "competitor2Name": "Cavaliers",
            "competitor1Id": 9000,
            "competitor2Id": 9001,
            "tournamentId": 177,
            "matchStart": 1764100000,
            "moreBets": 1227,
            "sportId": 5,  # basketball
        },
        # Match con timestamp inválido: debe descartarse
        "10000": {
            "matchId": 10000,
            "title": "??? - ???",
            "competitor1Name": "X",
            "competitor2Name": "Y",
            "competitor1Id": 1,
            "competitor2Id": 2,
            "tournamentId": 99,
            "matchStart": "not-a-timestamp",
            "moreBets": 0,
            "sportId": 1,
        },
    },
    "tournaments": {
        "2": {"tournamentName": "Premier League"},
        "177": {"tournamentName": "NBA"},
    },
    "bets": {
        # Single-line shots Griezmann 1.5
        "627768244": {
            "betId": 627768244,
            "matchId": 69339436,
            "betType": 5712,
            "betTitle": "Número de tiros del jugador",
            "specialBetValue": "player=sr:player:85859|total=1.5",
            "outcomes": [1957934202, 1957934203],
        },
        # Alt SoT Bukayo Saka (3 líneas)
        "627768369": {
            "betId": 627768369,
            "matchId": 69339436,
            "betType": 3311,
            "betTitle": "Número de tiros a puerta del jugador (logros)",
            "specialBetValue": "variant=pre:playerprops:69339436:1479630",
            "outcomes": [1957900100, 1957900101, 1957900102],
        },
        # Match-level totals
        "625504052": {
            "betId": 625504052,
            "matchId": 69339436,
            "betType": 2749,
            "betTitle": "Número total de goles",
            "specialBetValue": "total=2.5",
            "outcomes": [1951000110, 1951000111],
        },
        # Mercado desconocido (betType no en MARKET_TYPES)
        "999999999": {
            "betId": 999999999,
            "matchId": 69339436,
            "betType": 99999,
            "betTitle": "Mercado exótico",
            "specialBetValue": None,
            "outcomes": [777],
        },
        # Bet con outcomes vacíos: debe descartarse
        "888888888": {
            "betId": 888888888,
            "matchId": 69339436,
            "betType": 5720,
            "betTitle": "Tiros a puerta sin outcomes",
            "specialBetValue": "player=sr:player:1|total=0.5",
            "outcomes": [],
        },
        # Bet de OTRO partido: debe descartarse
        "111111111": {
            "betId": 111111111,
            "matchId": 11111111,
            "betType": 5712,
            "betTitle": "tiros otro partido",
            "specialBetValue": "player=sr:player:9|total=0.5",
            "outcomes": [200],
        },
    },
    "outcomes": {
        "1957934202": {
            "label": "Antoine Griezmann más de 1,5",
            "code": "over",
        },
        "1957934203": {
            "label": "Antoine Griezmann menos de 1,5",
            "code": "under",
        },
        "1957900100": {
            "label": "Bukayo Saka 1+",
            "code": "pre:playerprops:69339436:1479630:1",
        },
        "1957900101": {
            "label": "Bukayo Saka 2+",
            "code": "pre:playerprops:69339436:1479630:2",
        },
        "1957900102": {
            "label": "Bukayo Saka 3+",
            "code": "pre:playerprops:69339436:1479630:3",
        },
        "1951000110": {"label": "Más de 2,5", "code": "over"},
        "1951000111": {"label": "Menos de 2,5", "code": "under"},
        "777": {"label": "X", "code": ""},
        "200": {"label": "Y", "code": ""},
    },
    "odds": {
        "1957934202": 1.88,
        "1957934203": 1.92,
        "1957900100": 1.30,
        "1957900101": 2.15,
        "1957900102": 4.50,
        "1951000110": 1.88,
        "1951000111": 1.92,
        "777": 5.0,
        "200": 1.5,
    },
}


# ---------------------------------------------------------------------------
# _extract_balanced_json
# ---------------------------------------------------------------------------


class TestExtractBalancedJson:
    def test_simple_object(self) -> None:
        text = 'foo = {"a": 1, "b": 2};'
        start = text.index("{")
        assert _extract_balanced_json(text, start) == '{"a": 1, "b": 2}'

    def test_nested_objects(self) -> None:
        text = '{"a": {"b": {"c": 1}}, "d": 2}'
        assert _extract_balanced_json(text, 0) == text

    def test_braces_in_strings_are_ignored(self) -> None:
        # Una llave dentro de un string no debe contar.
        text = '{"key": "valor con { llaves }", "x": 1}'
        assert _extract_balanced_json(text, 0) == text

    def test_escaped_quotes(self) -> None:
        # \\" dentro de un string no debe cerrar el string prematuramente.
        text = '{"name": "He said \\"hi\\"", "n": 5}'
        assert _extract_balanced_json(text, 0) == text

    def test_unclosed_raises(self) -> None:
        with pytest.raises(RuntimeError):
            _extract_balanced_json('{"a": 1', 0)

    def test_start_not_brace_raises(self) -> None:
        with pytest.raises(RuntimeError):
            _extract_balanced_json("not an object", 0)


# ---------------------------------------------------------------------------
# parse_football_matches
# ---------------------------------------------------------------------------


class TestParseFootballMatches:
    def test_returns_only_football_matches(self) -> None:
        out = parse_football_matches(SAMPLE_STATE)
        ids = [m.match_id for m in out]
        assert "69339436" in ids
        # NBA filtrado
        assert "70505000" not in ids
        # Timestamp inválido descartado
        assert "10000" not in ids

    def test_kickoff_is_utc_datetime(self) -> None:
        out = parse_football_matches(SAMPLE_STATE)
        m = next(x for x in out if x.match_id == "69339436")
        assert isinstance(m.kickoff, datetime)
        assert m.kickoff.tzinfo == timezone.utc

    def test_basic_metadata(self) -> None:
        out = parse_football_matches(SAMPLE_STATE)
        m = next(x for x in out if x.match_id == "69339436")
        assert m.home_team == "Arsenal"
        assert m.away_team == "Atlético Madrid"
        assert m.tournament_id == "2"
        assert m.tournament_name == "Premier League"
        assert m.more_bets == 565

    def test_empty_state(self) -> None:
        assert parse_football_matches({}) == []


# ---------------------------------------------------------------------------
# parse_match_bets
# ---------------------------------------------------------------------------


class TestParseMatchBets:
    def test_only_known_market_types(self) -> None:
        bets = parse_match_bets(SAMPLE_STATE, "69339436")
        market_keys = {b.market_key for b in bets}
        assert "player_shots" in market_keys
        assert "player_sot_alt" in market_keys
        assert "match_totals" in market_keys
        # Bet exótico filtrado:
        assert all(b.bet_type_id != 99999 for b in bets)

    def test_outcomes_have_odds(self) -> None:
        bets = parse_match_bets(SAMPLE_STATE, "69339436")
        for b in bets:
            for oc in b.outcomes:
                assert oc.odds > 0

    def test_filters_other_match(self) -> None:
        bets = parse_match_bets(SAMPLE_STATE, "69339436")
        bet_ids = {b.bet_id for b in bets}
        # bet del otro partido descartado
        assert "111111111" not in bet_ids
        # bet con outcomes vacíos descartado
        assert "888888888" not in bet_ids

    def test_market_id_to_string(self) -> None:
        bets = parse_match_bets(SAMPLE_STATE, 69339436)  # acepta int
        assert any(b.bet_id == "625504052" for b in bets)

    def test_empty_state(self) -> None:
        assert parse_match_bets({}, "69339436") == []


# ---------------------------------------------------------------------------
# parse_player_props
# ---------------------------------------------------------------------------


class TestParsePlayerProps:
    def _props(self) -> list[WinamaxPlayerProp]:
        bets = parse_match_bets(SAMPLE_STATE, "69339436")
        return parse_player_props(bets)

    def test_filters_to_player_markets_only(self) -> None:
        # match_totals NO es player prop → no se incluye
        for p in self._props():
            assert p.market_key in PLAYER_PROP_MARKETS

    def test_single_line_over_under(self) -> None:
        props = self._props()
        griez = [p for p in props if p.player_name == "Antoine Griezmann"]
        assert len(griez) == 2
        over = next(p for p in griez if p.side == "over")
        under = next(p for p in griez if p.side == "under")
        assert over.line == 1.5
        assert under.line == 1.5
        assert over.odds == pytest.approx(1.88)
        assert under.odds == pytest.approx(1.92)
        assert over.player_external_id == "85859"
        assert over.market_key == "player_shots"

    def test_alt_grid_extracts_player_and_lines(self) -> None:
        props = self._props()
        saka = [p for p in props if p.player_name == "Bukayo Saka"]
        assert len(saka) == 3
        # Saka 1+ → línea over/under equivalente 0.5
        # Saka 2+ → 1.5
        # Saka 3+ → 2.5
        lines = sorted(p.line for p in saka)
        assert lines == [0.5, 1.5, 2.5]
        assert all(p.side == "over" for p in saka)
        assert all(p.player_external_id == "1479630" for p in saka)
        assert all(p.market_key == "player_sot_alt" for p in saka)

    def test_odds_match(self) -> None:
        saka_2plus = next(
            p for p in self._props()
            if p.player_name == "Bukayo Saka" and p.line == 1.5
        )
        assert saka_2plus.odds == pytest.approx(2.15)

    def test_no_match_totals_in_output(self) -> None:
        props = self._props()
        labels = [p.raw_label for p in props]
        # Las cuotas "Más de 2,5" del mercado match_totals NO deben aparecer.
        assert "Más de 2,5" not in labels
        assert "Menos de 2,5" not in labels

    def test_empty_input(self) -> None:
        assert parse_player_props([]) == []


# ---------------------------------------------------------------------------
# Sanity: la tabla de mapeo está bien formada
# ---------------------------------------------------------------------------


class TestMarketTypesTable:
    def test_market_types_keys_are_int(self) -> None:
        assert all(isinstance(k, int) for k in MARKET_TYPES.keys())

    def test_market_types_values_unique(self) -> None:
        # Aceptamos algún colision si está documentado, pero por defecto
        # cada betType debe tener una clave canónica única.
        values = list(MARKET_TYPES.values())
        assert len(values) == len(set(values))

    def test_player_prop_subset_consistent(self) -> None:
        # Todas las claves de PLAYER_PROP_MARKETS deben estar en MARKET_TYPES
        all_keys = set(MARKET_TYPES.values())
        assert PLAYER_PROP_MARKETS.issubset(all_keys)
