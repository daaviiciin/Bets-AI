"""Normalización de etiquetas Winamax → claves Odds API / ValueDetector."""

from __future__ import annotations

import re

from backend.services.match_results_updater import _names_match, _normalize as _nm

# Mercados Winamax conocidos → clave canónica en tabla ``markets`` / detector.
CANONICAL_MARKET_KEY: dict[str, str] = {
    "match_winner": "h2h",
    "match_totals": "totals",
    "match_btts": "btts",
    "match_double_chance": "double_chance",
}

# Mercados de verdad "más/menos de N" (sus resultados se normalizan a Over/Under).
# Par/Impar y "mitad con más goles" NO van aquí: sus resultados no son Over/Under
# y forzarlos colapsaba todas las opciones en "Under".
TOTALS_MARKETS: frozenset[str] = frozenset({
    "totals",
    "team_home_totals_goals",
    "team_away_totals_goals",
    "match_cards_totals",
    "match_corners_totals",
    "match_shots_totals",
    "match_sot_totals",
    "team_home_sot_totals",
    "team_away_sot_totals",
    "match_first_half_totals",
    "team_home_first_half_totals_goals",
    "team_away_first_half_totals_goals",
    "match_first_half_corners_totals",
    "match_first_half_cards_totals",
    "match_second_half_totals",
    "team_home_second_half_totals_goals",
    "team_away_second_half_totals_goals",
    "match_second_half_corners_totals",
    "match_second_half_cards_totals",
})

_EMPATE = frozenset({"empate", "draw", "tie", "x"})

_RE_TOT_LINE = re.compile(r"([\d]+[.,][\d]+|\d+)\s*$")
_RE_MAS = re.compile(r"m[áa]s\s+de|over", re.I)
_RE_MENOS = re.compile(r"menos\s+de|under", re.I)


def canonical_market_key(winamax_internal_key: str) -> str:
    """Devuelve la clave de mercado persistida en BD (alineada con Odds API)."""
    return CANONICAL_MARKET_KEY.get(winamax_internal_key, winamax_internal_key)


def normalize_match_outcome(
    canon: str, label: str, *, home_team: str, away_team: str,
) -> str:
    """Nombre de resultado para un mercado a nivel partido (clave canónica ``canon``).

    Los mercados sin normalización propia conservan la etiqueta de Winamax tal cual.
    """
    if canon == "h2h":
        return normalize_h2h_outcome(label, home_team=home_team, away_team=away_team)
    if canon in TOTALS_MARKETS:
        return normalize_totals_outcome(label)
    if canon == "btts":
        return normalize_btts_outcome(label)
    if canon == "double_chance":
        return normalize_double_chance_outcome(label, home_team=home_team, away_team=away_team)
    return label


def normalize_h2h_outcome(label: str, *, home_team: str, away_team: str) -> str:
    """``label`` español/Inglés → ``Draw`` o nombre equipo exacto (BD)."""
    raw = (label or "").strip()
    low = raw.lower()
    tok = {_normalize_piece(x) for x in low.replace(",", " ").split()}
    if tok <= _EMPATE or low in {"empate", "draw"}:
        return "Draw"
    hn = _nm(home_team)
    an = _nm(away_team)
    if _names_match(hn, _nm(raw)) or _substring_team(raw, home_team):
        return home_team
    if _names_match(an, _nm(raw)) or _substring_team(raw, away_team):
        return away_team
    return raw


def _normalize_piece(s: str) -> str:
    return s.strip().lower()


def _substring_team(raw: str, team: str) -> bool:
    return _nm(team) in _nm(raw)


def normalize_totals_outcome(label: str) -> str:
    ll = (label or "").strip().lower()
    if _RE_MAS.search(ll) or ll.startswith("+") or ll.startswith("más"):
        return "Over"
    if _RE_MENOS.search(ll) or ll.startswith("-"):
        return "Under"
    if ll in {"over"}:
        return "Over"
    if ll in {"under"}:
        return "Under"
    return "Over" if ("más" in ll or "mas " in ll) else "Under"


def parse_totals_line_from_label(label: str) -> float | None:
    m = _RE_TOT_LINE.search((label or "").replace(",", "."))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def normalize_btts_outcome(label: str) -> str:
    ll = (label or "").strip().lower()
    if ll in {"sí", "si", "yes", "y"}:
        return "Yes"
    if ll in {"no"}:
        return "No"
    return label.strip()[:1].upper() + label.strip()[1:] if label else "Yes"


def highest_scoring_half_side(label: str) -> str | None:
    """"Mitad con más goles": ``1ª mitad`` → ``1h``, ``Igual`` → ``tie``, ``2ª mitad`` → ``2h``.

    El resultado se guarda con la etiqueta de Winamax; esto solo la interpreta (modelo y
    liquidación). ``None`` si no se reconoce.
    """
    low = (label or "").strip().lower()
    if low in {"igual", "empate", "tie", "draw", "x"}:
        return "tie"
    if low.startswith(("1", "primera")) or "1st" in low or "first" in low:
        return "1h"
    if low.startswith(("2", "segunda")) or "2nd" in low or "second" in low:
        return "2h"
    return None


def normalize_double_chance_outcome(label: str, *, home_team: str, away_team: str) -> str:
    """Produce strings reconocidas por ``ValueDetector._double_chance_probability``."""
    ll = (label or "").strip()
    low = ll.lower()
    hn, an = home_team.strip(), away_team.strip()
    hl, al = hn.lower(), an.lower()

    if low in {"12", "1-2"} or ("sin empate" in low) or ("no draw" in low):
        return f"{hn} or {an}"

    if low in {"1x", "1-x", "1 x"}:
        return f"{hn} or Draw"
    if low in {"x2", "x-2", "x 2"}:
        return f"Draw or {an}"

    has_home = hl in low or _nm(hn) in _nm(low)
    has_away = al in low or _nm(an) in _nm(low)
    has_draw = "empate" in low or "draw" in low

    if has_home and has_draw and not has_away:
        return f"{hn} or Draw"
    if has_away and has_draw and not has_home:
        return f"Draw or {an}"
    if has_home and has_away and not has_draw:
        return f"{hn} or {an}"

    return ll
