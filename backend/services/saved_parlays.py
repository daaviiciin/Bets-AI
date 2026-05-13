"""Persistencia de combinadas y reaplicación de marcadores contra la BD."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import Match, SavedParlay
from backend.services.saved_parlay_grading import grade_single_leg, overall_from_leg_results


def snapshots_from_payload(legs: list[Any]) -> list[dict[str, Any]]:
    """Lista JSON-friendly para PostgreSQL."""
    out: list[dict[str, Any]] = []
    for leg in legs:
        raw = (
            leg.model_dump(mode="json")
            if hasattr(leg, "model_dump")
            else dict(leg)
        )
        out.append(raw)
    return out


def combine_products(legs: list[dict[str, Any]]) -> tuple[Decimal, Decimal | None]:
    odds_prod = Decimal("1")
    prob_prod = Decimal("1")
    all_probs = True
    for lg in legs:
        odds_prod *= Decimal(str(lg["offered_odds"]))
        p = lg.get("model_probability")
        if p is None:
            all_probs = False
        else:
            prob_prod *= Decimal(str(p))
    return (
        odds_prod.quantize(Decimal("0.0001")),
        prob_prod.quantize(Decimal("0.00000001")) if all_probs else None,
    )


def refresh_one_parlay(session: Session, row: SavedParlay) -> None:
    mids = sorted({int(lg["match_id"]) for lg in row.legs_snapshot})
    stmt = (
        select(Match)
        .where(Match.id.in_(mids))
        .options(joinedload(Match.home_team), joinedload(Match.away_team))
    )
    by_mid = {m.id: m for m in session.scalars(stmt).all()}
    graded: list[dict[str, Any]] = []
    for lg in row.legs_snapshot:
        mid = int(lg["match_id"])
        g = grade_single_leg(dict(lg), by_mid.get(mid))
        graded.append({**dict(lg), **g})

    ov = overall_from_leg_results([str(x["result"]) for x in graded])
    row.grading_snapshot = {"legs": graded}
    row.overall_result = ov
    row.last_graded_at = datetime.now(timezone.utc)


def refresh_all_parlays_for_user(session: Session, user_id: int) -> int:
    rows = session.scalars(
        select(SavedParlay).where(SavedParlay.user_id == user_id).order_by(SavedParlay.id),
    ).all()
    for r in rows:
        refresh_one_parlay(session, r)
    session.commit()
    return len(rows)
