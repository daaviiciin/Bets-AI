"""Apuestas (simples y combinadas) guardadas por el usuario actual: alta, corrección y resumen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from backend.app.database import get_db
from backend.app.deps.auth_user import get_current_user
from backend.app.schemas.responses import (
    SavedParlayCreateIn,
    SavedParlayOut,
    SavedParlayUpdateIn,
    UserParlayMetricsOut,
)
from backend.models import AppUser, SavedParlay
from backend.services.saved_parlays import (
    beneficio_apuesta,
    combine_products,
    refresh_one_parlay,
    refresh_all_parlays_for_user,
    resumen_apuestas,
    snapshots_from_payload,
)

router = APIRouter(prefix="/me/parlays", tags=["mis-apuestas"])


def _serialize(row: SavedParlay) -> SavedParlayOut:
    out = SavedParlayOut.model_validate(row)
    out.profit = beneficio_apuesta(row.overall_result, row.stake, row.combined_odds)
    return out


@router.get("/metrics-summary", response_model=UserParlayMetricsOut)
def parlay_metrics_summary(
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserParlayMetricsOut:
    rows = db.scalars(select(SavedParlay).where(SavedParlay.user_id == user.id)).all()
    return UserParlayMetricsOut(**resumen_apuestas(list(rows)))


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
        stake=body.stake,
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


def _own_parlay(db: Session, user: AppUser, parlay_id: int) -> SavedParlay:
    row = db.scalar(
        select(SavedParlay).where(
            SavedParlay.id == parlay_id,
            SavedParlay.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Apuesta no encontrada.")
    return row


@router.patch("/{parlay_id}", response_model=SavedParlayOut)
def update_parlay(
    parlay_id: int,
    body: SavedParlayUpdateIn,
    user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedParlayOut:
    """Cambia el importe o fija el resultado a mano (``manual_result: null`` lo devuelve al automático)."""

    row = _own_parlay(db, user, parlay_id)
    sent = body.model_fields_set
    if "stake" in sent:
        row.stake = body.stake
    if "manual_result" in sent:
        row.manual_result = body.manual_result
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
    row = _own_parlay(db, user, parlay_id)
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

    row = _own_parlay(db, user, parlay_id)
    refresh_one_parlay(db, row)
    db.commit()
    db.refresh(row)
    return _serialize(row)
