"""Generación de hasta 3 picks diarios (confianza alta) y corrección con marcador."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import FeaturedDailyPick, Match
from backend.services import value_calculator as vc
from backend.services.saved_parlay_grading import grade_single_leg
from backend.services.value_detector import ValueBet, ValueDetector

MAX_PICKS = 3
# Singles mostradas en transparencia: preferimos cuotas bajas (más “probables”) alineadas con confianza del modelo.
MAX_FEATURED_OFFERED_ODDS = Decimal("2")


def _dec_str(d: Decimal | None) -> str | None:
    if d is None:
        return None
    return format(d, "f")


def value_bet_to_leg_dict(b: ValueBet) -> dict[str, Any]:
    """Dict alineado con patas de combinada + metadatos de transparencia."""
    return {
        "match_id": b.match_id,
        "home_team": b.home_team,
        "away_team": b.away_team,
        "league": b.league,
        "commence_time": b.commence_time.isoformat(),
        "market_key": b.market_key,
        "outcome_name": b.outcome_name,
        "outcome_point": _dec_str(b.outcome_point),
        "player_name": b.player_name,
        "offered_odds": str(b.offered_odds),
        "model_probability": _dec_str(b.model_probability),
        "ev_market": str(b.ev),
        "model_ev": _dec_str(b.model_ev),
        "confidence": b.confidence,
    }


def select_top_distinct_matches(bets: list[ValueBet], *, max_picks: int = MAX_PICKS) -> list[ValueBet]:
    """Entre value bets de confianza **alta**, ordena por EV modelo y toma partidos distintos.

    Nunca elige una cuota > ``MAX_RECOMMENDABLE_ODDS`` (el detector ya las excluye).
    """

    high = [
        b for b in bets
        if b.confidence == "high" and vc.is_recommendable_odds(b.offered_odds)
    ]

    def sort_key(b: ValueBet) -> tuple[float, float]:
        me = float(b.model_ev) if b.model_ev is not None else -999.0
        return (-me, -float(b.ev))

    high.sort(key=sort_key)
    picked: list[ValueBet] = []
    seen: set[int] = set()
    for b in high:
        if b.match_id in seen:
            continue
        picked.append(b)
        seen.add(b.match_id)
        if len(picked) >= max_picks:
            break
    return picked


def _refresh_one_row(session: Session, row: FeaturedDailyPick) -> None:
    if not row.picks_json:
        row.grading_snapshot = None
        row.last_graded_at = datetime.now(timezone.utc)
        return

    mids = sorted({int(lg["match_id"]) for lg in row.picks_json})
    stmt = (
        select(Match)
        .where(Match.id.in_(mids))
        .options(joinedload(Match.home_team), joinedload(Match.away_team))
    )
    by_mid = {m.id: m for m in session.scalars(stmt).all()}
    graded: list[dict[str, Any]] = []
    for lg in row.picks_json:
        mid = int(lg["match_id"])
        g = grade_single_leg(dict(lg), by_mid.get(mid))
        graded.append({**dict(lg), **g})

    row.grading_snapshot = {"legs": graded}
    row.last_graded_at = datetime.now(timezone.utc)


def refresh_grading_for_row(session: Session, row: FeaturedDailyPick) -> None:
    _refresh_one_row(session, row)


def refresh_all_grading(session: Session) -> int:
    """Re-evalúa todas las filas con datos de ``matches`` (p. ej. tras pipeline diario)."""

    rows = session.scalars(
        select(FeaturedDailyPick).order_by(FeaturedDailyPick.pick_date.desc()),
    ).all()
    for r in rows:
        if r.picks_json:
            _refresh_one_row(session, r)
        else:
            r.grading_snapshot = None
            r.last_graded_at = datetime.now(timezone.utc)
    session.commit()
    return len(rows)


def generate_picks_for_date(session: Session, pick_date: date) -> FeaturedDailyPick:
    """Genera snapshot de picks (idempotente por ``pick_date`` único)."""

    detector = ValueDetector(
        session,
        min_ev=Decimal("0.02"),
        min_ev_model=Decimal("0"),
        require_model_agreement=True,
    )
    bets = detector.detect_upcoming(horizon_hours=14 * 24, league_sport_key=None)
    chosen = select_top_distinct_matches(bets)
    legs = [value_bet_to_leg_dict(b) for b in chosen]
    row = FeaturedDailyPick(
        pick_date=pick_date,
        picks_json=legs,
        grading_snapshot=None,
        last_graded_at=None,
    )
    session.add(row)
    session.flush()
    if legs:
        _refresh_one_row(session, row)
    else:
        row.last_graded_at = datetime.now(timezone.utc)
    return row


def ensure_row_for_date(session: Session, pick_date: date) -> FeaturedDailyPick:
    existing = session.scalar(select(FeaturedDailyPick).where(FeaturedDailyPick.pick_date == pick_date))
    if existing is not None:
        return existing
    return generate_picks_for_date(session, pick_date)


def aggregate_leg_metrics(session: Session) -> dict[str, Any]:
    """Cuenta patas por resultado (incluye histórico con snapshot corregido)."""

    rows = session.scalars(select(FeaturedDailyPick)).all()
    hits = misses = pending = review = 0
    total_legs = 0
    for row in rows:
        src = row.grading_snapshot
        if src and isinstance(src.get("legs"), list):
            legs_iter = src["legs"]
        else:
            legs_iter = row.picks_json
        for lg in legs_iter:
            total_legs += 1
            res = (lg.get("result") if isinstance(lg, dict) else None) or "pending"
            if res == "hit":
                hits += 1
            elif res == "miss":
                misses += 1
            elif res == "needs_review":
                review += 1
            else:
                pending += 1
    settled = hits + misses
    rate = round((1000.0 * hits / settled)) / 10.0 if settled > 0 else None
    return {
        "total_pick_days": len(rows),
        "total_legs": total_legs,
        "hits": hits,
        "misses": misses,
        "pending": pending,
        "needs_review": review,
        "rate_pct": rate,
    }


__all__ = [
    "MAX_PICKS",
    "MAX_FEATURED_OFFERED_ODDS",
    "aggregate_leg_metrics",
    "ensure_row_for_date",
    "generate_picks_for_date",
    "refresh_all_grading",
    "refresh_grading_for_row",
    "select_top_distinct_matches",
    "value_bet_to_leg_dict",
]
