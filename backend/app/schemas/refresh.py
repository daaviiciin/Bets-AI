"""Schemas para el endpoint de refresh."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RefreshSummaryOut(BaseModel):
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    ok: bool
    leagues_total: int
    leagues_ok: int
    leagues_error: int
    snapshots_deleted: int
    past_matches_deleted: int
    matches_inserted: int
    odds_inserted: int
    requests_used_total: int | None
    requests_remaining: int | None
    errors: list[str]
