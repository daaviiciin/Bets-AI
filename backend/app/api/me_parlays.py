"""CRUD combinadas guardadas por el usuario actual."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from backend.app.database import get_db
from backend.app.deps.auth_user import get_current_user
from backend.app.schemas.responses import SavedParlayCreateIn, SavedParlayOut, UserParlayMetricsOut
from backend.models import AppUser, SavedParlay
from backend.services.saved_parlays import (
    combine_products,
    refresh_one_parlay,
    refresh_all_parlays_for_user,
    snapshots_from_payload,
)

router = APIRouter(prefix="/me/parlays", tags=["mis-apuestas"])


def _serialize(row: SavedParlay) -> SavedParlayOut:
    out = SavedParlayOut.model_validate(row)
    return out


def _user_parlay_metrics(rows: list[SavedParlay]) -> UserParlayMetricsOut:
    total = len(rows)
    pending = sum(1 for r in rows if r.overall_result == "pending")
    needs_review = sum(1 for r in rows if r.overall_result == "needs_review")
    hits = sum(1 for r in rows if r.overall_result == "hit")
    misses = sum(1 for r in rows if r.overall_result == "miss")
    settled = hits + misses
    rate = round((1000.0 * hits / settled)) / 10.0 if settled else None
    return UserParlayMetricsOut(
        total=total,
        pending=pending,
        needs_review=needs_review,
        hits=hits,
        misses=misses,
        rate_pct=rate,
    )


@router.get("/metrics-summary", response_model=UserParlayMetricsOut)
def parlay_metrics_summary(
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserParlayMetricsOut:
    rows = db.scalars(select(SavedParlay).where(SavedParlay.user_id == user.id)).all()
    return _user_parlay_metrics(list(rows))


@router.get("", response_model=list[SavedParlayOut])
def list_saved_parlays(
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SavedParlayOut]:
    rows = db.scalars(
        select(SavedParlay).where(SavedParlay.user_id == user.id).order_by(SavedParlay.id.desc()),
    ).all()
    return [_serialize(r) for r in rows]


@router.post("", response_model=SavedParlayOut)
def save_parlay(
    body: SavedParlayCreateIn,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedParlayOut:
    legs = snapshots_from_payload(body.legs)
    odds_comb, probs_comb = combine_products(legs)
    ttl = None
    if body.title and body.title.strip():
        ttl = body.title.strip()[:160]

    row = SavedParlay(
        user_id=user.id,
        title=ttl,
        combined_odds=odds_comb,
        model_joint_probability=probs_comb,
        legs_snapshot=legs,
        grading_snapshot=None,
        overall_result="pending",
        last_graded_at=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    refresh_one_parlay(db, row)
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.delete("/{parlay_id}")
def delete_parlay(
    parlay_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.scalar(
        select(SavedParlay).where(
            SavedParlay.id == parlay_id,
            SavedParlay.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Combinada no encontrada.")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh-grades")
def refresh_all_grades(
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Recalcula todas las combinadas desde las tablas ``matches``."""

    count = refresh_all_parlays_for_user(db, user.id)
    return {"updated_parlays": count}


@router.post("/{parlay_id}/refresh-grade", response_model=SavedParlayOut)
def refresh_one_grade(
    parlay_id: int,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedParlayOut:
    """Recalcula el resultado de una combinada desde las tablas ``matches``."""

    row = db.scalar(
        select(SavedParlay).where(
            SavedParlay.id == parlay_id,
            SavedParlay.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Combinada no encontrada.")
    refresh_one_parlay(db, row)
    db.commit()
    db.refresh(row)
    return _serialize(row)
