"""Tests del parser de Winamax (PRELOADED_STATE).

Fixtures sintéticos basados en respuestas reales observadas.
Validamos:
* extracción de balanced JSON tolerando llaves en strings,
* parseo de partidos de fútbol,
* parseo de mercados y filtrado por betType,
* extracción de player props con líneas, jugador y sentido over/under.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.scrapers.winamax import (
    LEGACY_MARKET_TYPE_IDS,
    MARKET_TYPES,
    PLAYER_PROP_MARKETS,
    WinamaxPlayerProp,
    _extract_balanced_json,
    parse_football_matches,
    parse_match_bets,
    parse_player_props,
)
from backend.services.winamax_normalization import TOTALS_MARKETS, normalize_match_outcome
from backend.services.winamax_odds_ingester import WinamaxOddsIngester


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
        # Colisiones solo las documentadas (ids antiguos de Winamax); el resto, una clave
        # canónica por betType.
        values = [v for k, v in MARKET_TYPES.items() if k not in LEGACY_MARKET_TYPE_IDS]
        assert len(values) == len(set(values))

    def test_player_prop_subset_consistent(self) -> None:
        # Todas las claves de PLAYER_PROP_MARKETS deben estar en MARKET_TYPES
        all_keys = set(MARKET_TYPES.values())
        assert PLAYER_PROP_MARKETS.issubset(all_keys)


# ---------------------------------------------------------------------------
# Pestaña "Estad. jugadores" (formato de Winamax.es el 2026-10-19)
# ---------------------------------------------------------------------------

# Recorte real de data/raw/winamax_muestras/match_72221330_state.json (Tottenham - Coventry):
# un mercado por escalón ("count=N") y un outcome por jugador con su id de Sportradar.
STATS_JUGADORES_STATE: dict = {
    "bets": {
        "717981157": {
            "betId": 717981157, "matchId": 72221330, "betType": 6864,
            "betTitle": "Realiza 1 o más remates a puerta", "specialBetValue": "count=1",
            "outcomes": [2225674155, 2225674175],
        },
        "717981189": {
            "betId": 717981189, "matchId": 72221330, "betType": 6864,
            "betTitle": "Realiza 2 o más remates a puerta", "specialBetValue": "count=2",
            "outcomes": [2225675862],
        },
        "717981158": {
            "betId": 717981158, "matchId": 72221330, "betType": 6921,
            "betTitle": "Realiza 1 o más paradas", "specialBetValue": "count=1",
            "outcomes": [2225674187, 2225674186],
        },
        "717981152": {
            "betId": 717981152, "matchId": 72221330, "betType": 6921,
            "betTitle": "Realiza 3 o más paradas", "specialBetValue": "count=3",
            "outcomes": [2225673905],
        },
        "717981185": {
            "betId": 717981185, "matchId": 72221330, "betType": 6858,
            "betTitle": "Realiza 1 o más remates", "specialBetValue": "count=1",
            "outcomes": [2225675577],
        },
        "717981166": {
            "betId": 717981166, "matchId": 72221330, "betType": 6933,
            "betTitle": "El jugador recibe una o más tarjetas",
            "outcomes": [2225674516],
        },
    },
    "outcomes": {
        "2225674155": {"label": "Dominic Solanke", "code": "sr:player:361420", "team": 1},
        "2225674175": {"label": "Omar Marmoush", "code": "sr:player:1107697", "team": 1},
        "2225675862": {"label": "Dominic Solanke", "code": "sr:player:361420", "team": 1},
        "2225674187": {"label": "Carl Rushworth", "code": "sr:player:2186020", "team": 2},
        "2225674186": {"label": "Martin Dubravka", "code": "sr:player:42209", "team": 1},
        "2225673905": {"label": "Carl Rushworth", "code": "sr:player:2186020", "team": 2},
        "2225675577": {"label": "Dominic Solanke", "code": "sr:player:361420", "team": 1},
        "2225674516": {"label": "Frank Onyeka", "code": "sr:player:959744", "team": 2},
    },
    "odds": {
        "2225674155": 1.37, "2225674175": 1.43, "2225675862": 2.6,
        "2225674187": 1.01, "2225674186": 1.06, "2225673905": 1.33,
        "2225675577": 1.02, "2225674516": 3.75,
    },
}


class TestEstadisticasDeJugador:
    def _props(self) -> list[WinamaxPlayerProp]:
        return parse_player_props(parse_match_bets(STATS_JUGADORES_STATE, "72221330"))

    def test_ids_de_mercado_actuales_mapeados(self) -> None:
        assert MARKET_TYPES[6864] == "player_sot_alt"
        assert MARKET_TYPES[6858] == "player_shots_alt"
        assert MARKET_TYPES[6921] == "player_saves_alt"
        assert MARKET_TYPES[6933] == "player_card_yes_no"
        assert "player_saves_alt" in PLAYER_PROP_MARKETS

    def test_1_o_mas_tiros_a_puerta_es_mas_de_0_5(self) -> None:
        solanke = next(
            p for p in self._props()
            if p.market_key == "player_sot_alt" and p.player_name == "Dominic Solanke"
            and p.line == 0.5
        )
        assert solanke.side == "over"
        assert solanke.odds == pytest.approx(1.37)
        assert solanke.player_external_id == "361420"

    def test_2_o_mas_tiros_a_puerta_es_mas_de_1_5(self) -> None:
        lineas = sorted(
            p.line for p in self._props()
            if p.market_key == "player_sot_alt" and p.player_name == "Dominic Solanke"
        )
        assert lineas == [0.5, 1.5]

    def test_paradas_del_portero_por_escalon(self) -> None:
        rushworth = sorted(
            (p.line, p.odds) for p in self._props()
            if p.market_key == "player_saves_alt" and p.player_name == "Carl Rushworth"
        )
        assert rushworth == [(0.5, pytest.approx(1.01)), (2.5, pytest.approx(1.33))]

    def test_tiros_totales_y_tarjeta(self) -> None:
        props = self._props()
        assert any(p.market_key == "player_shots_alt" and p.line == 0.5 for p in props)
        tarjeta = next(p for p in props if p.market_key == "player_card_yes_no")
        assert tarjeta.player_name == "Frank Onyeka"
        assert tarjeta.side == "yes"

    def test_cuenta_todos_los_outcomes(self) -> None:
        assert len(self._props()) == 8

    def test_marca_2_o_mas_ignora_los_de_3_y_4_goles(self) -> None:
        state = {
            "bets": {
                str(i): {
                    "betId": i, "matchId": 1, "betType": 3876,
                    "betTitle": f"Marca {n} goles o más", "specialBetValue": f"goals={n}",
                    "outcomes": [i * 10],
                }
                for i, n in ((1, 2), (2, 3), (3, 4))
            },
            "outcomes": {str(i * 10): {"label": "Dominic Solanke"} for i in (1, 2, 3)},
            "odds": {"10": 7.5, "20": 32, "30": 110},
        }
        props = parse_player_props(parse_match_bets(state, "1"))
        assert [(p.market_key, p.odds) for p in props] == [("player_to_score_2plus", 7.5)]


# ---------------------------------------------------------------------------
# Tiros a puerta por equipo (betType 6206 local / 6207 visitante)
# ---------------------------------------------------------------------------

_MUESTRA_72221330 = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "winamax_muestras"
    / "match_72221330_state.json"
)

# Recorte real de match_72221330 (Tottenham - Coventry City): bets del state.json y
# outcomes/odds del PRELOADED_STATE de match_72221330.html (el state.json no los trae).
TIROS_PUERTA_EQUIPO_STATE: dict = {
    "matches": {"72221330": {"competitor1Name": "Tottenham", "competitor2Name": "Coventry City"}},
    "bets": {
        "722627825": {
            "betId": 722627825, "matchId": 72221330, "betType": 6206,
            "betTitle": "Número de tiros a puerta por Tottenham", "specialBetValue": "total=2.5",
            "outcomes": [2239600447, 2239600448],
        },
        "722627831": {
            "betId": 722627831, "matchId": 72221330, "betType": 6207,
            "betTitle": "Número de tiros a puerta por Coventry City",
            "specialBetValue": "total=1.5",
            "outcomes": [2239600459, 2239600460],
        },
    },
    "outcomes": {
        "2239600447": {"label": "Más de 2,5", "code": "12"},
        "2239600448": {"label": "Menos de 2,5", "code": "13"},
        "2239600459": {"label": "Más de 1,5", "code": "12"},
        "2239600460": {"label": "Menos de 1,5", "code": "13"},
    },
    "odds": {"2239600447": 1.04, "2239600448": 8, "2239600459": 1.12, "2239600460": 4.7},
}


class TestTirosAPuertaPorEquipo:
    def _bets(self) -> dict:
        return {b.market_key: b for b in parse_match_bets(TIROS_PUERTA_EQUIPO_STATE, "72221330")}

    def test_6206_es_local_y_6207_visitante(self) -> None:
        assert MARKET_TYPES[6206] == "team_home_sot_totals"
        assert MARKET_TYPES[6207] == "team_away_sot_totals"
        bets = self._bets()
        assert "Tottenham" in bets["team_home_sot_totals"].title
        assert "Coventry City" in bets["team_away_sot_totals"].title

    def test_outcomes_mas_menos_con_su_linea(self) -> None:
        bets = self._bets()
        filas = []
        for key in ("team_home_sot_totals", "team_away_sot_totals"):
            bet = bets[key]
            line = WinamaxOddsIngester._line_from_special_value(bet.special_bet_value)
            for oc in bet.outcomes:
                nombre = normalize_match_outcome(
                    key, oc.label, home_team="Tottenham", away_team="Coventry City",
                )
                filas.append((key, nombre, line, oc.odds))
        assert filas == [
            ("team_home_sot_totals", "Over", 2.5, pytest.approx(1.04)),
            ("team_home_sot_totals", "Under", 2.5, pytest.approx(8)),
            ("team_away_sot_totals", "Over", 1.5, pytest.approx(1.12)),
            ("team_away_sot_totals", "Under", 1.5, pytest.approx(4.7)),
        ]

    def test_son_mercados_de_totales(self) -> None:
        assert {"team_home_sot_totals", "team_away_sot_totals"} <= TOTALS_MARKETS

    @pytest.mark.skipif(not _MUESTRA_72221330.exists(), reason="muestra local no versionada")
    def test_muestra_completa_local_y_visitante(self) -> None:
        state = json.loads(_MUESTRA_72221330.read_text(encoding="utf-8"))
        partido = state["matches"]["72221330"]
        local, visitante = partido["competitor1Name"], partido["competitor2Name"]
        titulos: dict[int, set[str]] = {}
        for b in state["bets"].values():
            if b.get("betType") in (6206, 6207):
                titulos.setdefault(b["betType"], set()).add(b["betTitle"])
        assert titulos[6206] == {f"Número de tiros a puerta por {local}"}
        assert titulos[6207] == {f"Número de tiros a puerta por {visitante}"}

    @pytest.mark.skipif(not _MUESTRA_72221330.exists(), reason="muestra local no versionada")
    def test_muestra_sin_total_de_goles_de_la_segunda_mitad(self) -> None:
        # Documenta por qué match_second_half_totals sigue sin betType: Winamax no lo publica
        # prepartido (tampoco en las muestras del 2026-10-25). El mercado de 2ª parte que hay
        # es "Mitad con más goles" (3317). Si aparece, hay que mapearlo con su id real.
        state = json.loads(_MUESTRA_72221330.read_text(encoding="utf-8"))
        titulos = {b.get("betTitle", "") for b in state["bets"].values()}
        assert not any("2ª mitad" in t and "total de goles" in t.lower() for t in titulos)


# ---------------------------------------------------------------------------
# "Mitad con más goles" (betType 3317)
# ---------------------------------------------------------------------------

# Recorte real de match_72478662 (Sevilla FC - Osasuna), PRELOADED_STATE del 2026-10-25.
MITAD_CON_MAS_GOLES_STATE: dict = {
    "matches": {"72478662": {"competitor1Name": "Sevilla FC", "competitor2Name": "Osasuna"}},
    "bets": {
        "716800350": {
            "betId": 716800350, "matchId": 72478662, "betType": 3317,
            "betTitle": "Mitad con más goles", "specialBetValue": None,
            "outcomes": [2221772598, 2221772600, 2221772599],
        },
    },
    "outcomes": {
        "2221772598": {"betId": 716800350, "label": "1ª mitad", "code": "1"},
        "2221772600": {"betId": 716800350, "label": "Igual", "code": "2"},
        "2221772599": {"betId": 716800350, "label": "2ª mitad", "code": "3"},
    },
    "odds": {"2221772598": 3.1, "2221772600": 3.1, "2221772599": 2.15},
}


class TestMitadConMasGoles:
    def test_3317_se_mapea_a_mitad_con_mas_goles(self) -> None:
        assert MARKET_TYPES[3317] == "match_highest_scoring_half"
        (bet,) = parse_match_bets(MITAD_CON_MAS_GOLES_STATE, "72478662")
        assert bet.market_key == "match_highest_scoring_half"
        assert bet.title == "Mitad con más goles"

    def test_outcomes_conservan_la_etiqueta_sin_linea(self) -> None:
        (bet,) = parse_match_bets(MITAD_CON_MAS_GOLES_STATE, "72478662")
        filas = [
            (
                normalize_match_outcome(
                    bet.market_key, oc.label, home_team="Sevilla FC", away_team="Osasuna",
                ),
                oc.odds,
            )
            for oc in bet.outcomes
        ]
        assert filas == [("1ª mitad", 3.1), ("Igual", 3.1), ("2ª mitad", 2.15)]
        assert WinamaxOddsIngester._line_from_special_value(bet.special_bet_value) is None

    def test_no_es_un_mercado_de_totales(self) -> None:
        # Si lo fuera, los tres resultados colapsarían en "Under".
        assert "match_highest_scoring_half" not in TOTALS_MARKETS