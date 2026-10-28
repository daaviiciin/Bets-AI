"""Persistencia de combinadas y reaplicación de marcadores contra la BD."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import Match, SavedParlay
from backend.services.saved_parlay_grading import (
    cargar_stats_partidos,
    cuota_efectiva,
    grade_single_leg,
    overall_from_leg_results,
)

#: Resultados que el usuario puede fijar a mano; ``void`` = nula (se devuelve el importe).
RESULTADOS_MANUALES = frozenset({"hit", "miss", "void"})


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
    stats = cargar_stats_partidos(session, by_mid.values())
    graded: list[dict[str, Any]] = []
    for lg in row.legs_snapshot:
        mid = int(lg["match_id"])
        g = grade_single_leg(dict(lg), by_mid.get(mid), stats.get(mid))
        graded.append({**dict(lg), **g})

    ov = overall_from_leg_results([str(x["result"]) for x in graded])
    # El automático se guarda siempre, para enseñar qué dijo la corrección aunque el usuario mande.
    snapshot: dict[str, Any] = {"legs": graded, "auto_result": ov}
    efectiva = cuota_efectiva(graded)
    if efectiva is not None:
        # Hay patas nulas: cuota sin ellas, para corregir a mano una combinada con nulas.
        snapshot["cuota_efectiva"] = str(efectiva)
    row.grading_snapshot = snapshot
    row.overall_result = row.manual_result or ov
    row.last_graded_at = datetime.now(timezone.utc)


def partidos_sin_liquidar(session: Session, user_id: int) -> set[int]:
    """Partidos de las patas de las apuestas del usuario que aún no están liquidadas."""
    rows = session.scalars(
        select(SavedParlay)
        .where(SavedParlay.user_id == user_id)
        .where(SavedParlay.overall_result.in_(("pending", "pending_stats"))),
    ).all()
    return {int(lg["match_id"]) for r in rows for lg in r.legs_snapshot if lg.get("match_id")}


def refresh_all_parlays_for_user(session: Session, user_id: int) -> int:
    rows = session.scalars(
        select(SavedParlay).where(SavedParlay.user_id == user_id).order_by(SavedParlay.id),
    ).all()
    for r in rows:
        refresh_one_parlay(session, r)
    session.commit()
    return len(rows)


def beneficio_apuesta(resultado: str, importe: Decimal | None, cuota: Decimal) -> Decimal | None:
    """Beneficio neto en euros de una apuesta ya liquidada, o ``None`` si no cuenta.

    ``resultado`` es ``overall_result`` (pending | pending_stats | hit | miss | needs_review | void), ``importe``
    lo apostado (``None`` en apuestas guardadas antes de existir el campo) y ``cuota`` la cuota
    combinada decimal.
    """
    # Sin importe no hay dinero que contar; pendientes y "a revisar" aún no están liquidadas.
    if importe is None or resultado not in ("hit", "miss", "void"):
        return None
    if resultado == "hit":
        return (importe * (cuota - 1)).quantize(Decimal("0.01"))
    if resultado == "miss":
        return -importe
    # Nula: se devuelve el importe. Cuenta como liquidada con 0 €, pero no entra en lo apostado del ROI.
    return Decimal("0")


def resumen_apuestas(rows: list[SavedParlay]) -> dict[str, Any]:
    """Aciertos, % de acierto y dinero (apostado, beneficio, ROI) de las apuestas de un usuario."""
    hits = sum(1 for r in rows if r.overall_result == "hit")
    misses = sum(1 for r in rows if r.overall_result == "miss")
    settled = hits + misses
    rate = round(1000.0 * hits / settled) / 10.0 if settled else None

    staked = Decimal("0")
    profit = Decimal("0")
    for r in rows:
        b = beneficio_apuesta(r.overall_result, r.stake, r.combined_odds)
        if b is None:
            continue
        profit += b
        if r.overall_result in ("hit", "miss") and r.stake is not None:
            staked += r.stake
    roi = (profit / staked * 100).quantize(Decimal("0.1")) if staked > 0 else None

    return {
        "total": len(rows),
        # "Esperando stats" cuenta como pendiente: el partido ya acabó pero falta el dato.
        "pending": sum(1 for r in rows if r.overall_result in ("pending", "pending_stats")),
        "needs_review": sum(1 for r in rows if r.overall_result == "needs_review"),
        "hits": hits,
        "misses": misses,
        "voids": sum(1 for r in rows if r.overall_result == "void"),
        "rate_pct": rate,
        "staked": staked.quantize(Decimal("0.01")),
        "profit": profit.quantize(Decimal("0.01")),
        "roi_pct": roi,
    }
