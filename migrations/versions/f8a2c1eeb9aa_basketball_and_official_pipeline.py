"""Puntos NBA/basket en team_season_stats, sigmas por liga y campos jugador.

Revision ID: f8a2c1eeb9aa
Revises: b0c965a52054
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a2c1eeb9aa"
down_revision: Union[str, None] = "b0c965a52054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leagues",
        sa.Column("bb_sd_margin", sa.Numeric(6, 3), nullable=True),
    )
    op.add_column(
        "leagues",
        sa.Column("bb_sd_team_points", sa.Numeric(6, 3), nullable=True),
    )
    op.add_column(
        "leagues",
        sa.Column("bb_sd_game_total", sa.Numeric(6, 3), nullable=True),
    )
    op.add_column(
        "team_season_stats",
        sa.Column("bb_points_for", sa.Integer(), nullable=True),
    )
    op.add_column(
        "team_season_stats",
        sa.Column("bb_points_against", sa.Integer(), nullable=True),
    )
    op.add_column(
        "player_season_stats",
        sa.Column("bb_points_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "player_season_stats",
        sa.Column("bb_rebounds_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "player_match_stats",
        sa.Column("bb_points", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("player_match_stats", "bb_points")
    op.drop_column("player_season_stats", "bb_rebounds_total")
    op.drop_column("player_season_stats", "bb_points_total")
    op.drop_column("team_season_stats", "bb_points_against")
    op.drop_column("team_season_stats", "bb_points_for")
    op.drop_column("leagues", "bb_sd_game_total")
    op.drop_column("leagues", "bb_sd_team_points")
    op.drop_column("leagues", "bb_sd_margin")
