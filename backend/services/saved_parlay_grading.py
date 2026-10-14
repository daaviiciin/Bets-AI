"""Corrección automática de patas (misma lógica que el frontend `grade-saved-bet`)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from backend.models.match import MATCH_STATUS_FINISHED, Match
from backend.services.match_results_updater import _normalize as _norm_team, _names_match
from backend.services.winamax_normalization import (
    canonical_market_key,
    normalize_btts_outcome,
    normalize_h2h_outcome,
    normalize_totals_outcome,
)

LegResult = Literal["pending", "hit", "miss", "needs_review"]


def _norm(s: str) -> str:
    return s.strip().lower().replace(".", "")


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grade_single_leg(leg: dict[str, Any], match: Match | None) -> dict[str, Any]:
    """Devuelve ``result``, ``score_text`` y datos mínimos de la pata."""
    home_name = (
        match.home_team.name if match and match.home_team else str(leg.get("home_team", ""))
    )
    away_name = (
        match.away_team.name if match and match.away_team else str(leg.get("away_team", ""))
    )
    hs = match.home_score if match else None
    aw = match.away_score if match else None

    score_text = (
        f"{home_name} {hs} · {away_name} {aw}"
        if (hs is not None and aw is not None)
        else None
    )

    if (
        match is None
        or match.status != MATCH_STATUS_FINISHED
        or hs is None
        or aw is None
    ):
        return {"result": "pending", "score_text": None, "match_id": leg.get("match_id")}

    tg = hs + aw
    raw_mk = str(leg.get("market_key", "")).strip()
    nk = _norm(canonical_market_key(raw_mk) if raw_mk else raw_mk)

    if nk.startswith("player_"):
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}

    if nk == "double_chance":
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}

    if nk.startswith("match_") and nk.endswith("_totals"):
        extras = ("corners", "cards", "shots", "sot")
        if any(x in nk for x in extras):
            return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}

    if nk == "h2h":
        picked = normalize_h2h_outcome(
            str(leg.get("outcome_name", "")),
            home_team=home_name,
            away_team=away_name,
        )
        if picked == "Draw":
            hit = hs == aw
            return {"result": "hit" if hit else "miss", "score_text": score_text, "match_id": leg.get("match_id")}
        if _names_match(_norm_team(picked), _norm_team(home_name)):
            hit = hs > aw
            return {"result": "hit" if hit else "miss", "score_text": score_text, "match_id": leg.get("match_id")}
        if _names_match(_norm_team(picked), _norm_team(away_name)):
            hit = aw > hs
            return {"result": "hit" if hit else "miss", "score_text": score_text, "match_id": leg.get("match_id")}
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}

    if nk == "btts":
        on = _norm(normalize_btts_outcome(str(leg.get("outcome_name", ""))))
        both = hs >= 1 and aw >= 1
        wants_yes = on in {"yes", "sí", "si"}
        wants_no = on == "no"
        if not wants_yes and not wants_no:
            return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}
        hit = both if wants_yes else not both
        return {"result": "hit" if hit else "miss", "score_text": score_text, "match_id": leg.get("match_id")}

    if nk not in {"totals", "team_home_totals_goals", "team_away_totals_goals"}:
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}

    pt = _to_float(leg.get("outcome_point"))
    on = _norm(normalize_totals_outcome(str(leg.get("outcome_name", ""))))
    if pt is None:
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}

    if nk == "team_home_totals_goals":
        actual = hs
    elif nk == "team_away_totals_goals":
        actual = aw
    else:
        actual = tg

    over = on == "over"
    under = on == "under"
    if not over and not under:
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}
    if actual == pt:
        return {"result": "needs_review", "score_text": score_text, "match_id": leg.get("match_id")}
    hit = actual > pt if over else actual < pt
    return {"result": "hit" if hit else "miss", "score_text": score_text, "match_id": leg.get("match_id")}


def overall_from_leg_results(results: list[str]) -> LegResult | str:
    if any(r == "miss" for r in results):
        return "miss"
    if any(r == "pending" for r in results):
        return "pending"
    if any(r == "needs_review" for r in results):
        return "needs_review"
    return "hit"
