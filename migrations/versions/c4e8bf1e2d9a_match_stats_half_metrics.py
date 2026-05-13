"""match_stats nullable columns for per-half metrics (Flashscore-derived).

Revision ID: c4e8bf1e2d9a
Revises: aa11bb22cc33
Create Date: 2026-05-13

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e8bf1e2d9a"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in (
        "goals_1h",
        "goals_2h",
        "corners_1h",
        "corners_2h",
        "yellow_cards_1h",
        "yellow_cards_2h",
        "red_cards_1h",
        "red_cards_2h",
    ):
        op.add_column("match_stats", sa.Column(col, sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in (
        "red_cards_2h",
        "red_cards_1h",
        "yellow_cards_2h",
        "yellow_cards_1h",
        "corners_2h",
        "corners_1h",
        "goals_2h",
        "goals_1h",
    ):
        op.drop_column("match_stats", col)
