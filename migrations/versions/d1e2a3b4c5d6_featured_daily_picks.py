"""Tabla picks diarios destacados (transparencia / métricas app).

Revision ID: d1e2a3b4c5d6
Revises: c4e8bf1e2d9a
Create Date: 2026-05-14

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2a3b4c5d6"
down_revision: str | None = "c4e8bf1e2d9a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "featured_daily_picks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pick_date", sa.Date(), nullable=False),
        sa.Column("picks_json", sa.JSON(), nullable=False),
        sa.Column("grading_snapshot", sa.JSON(), nullable=True),
        sa.Column("last_graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.UniqueConstraint("pick_date"),
    )


def downgrade() -> None:
    op.drop_table("featured_daily_picks")

