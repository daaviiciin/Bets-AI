"""match_sofascore_event_id

Revision ID: 20940d53e9b2
Revises: 3263e74e0603
Create Date: 2026-05-05 15:32:57.636278

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20940d53e9b2'
down_revision: Union[str, None] = '3263e74e0603'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("sofascore_event_id", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_matches_sofascore_event_id",
        "matches",
        ["sofascore_event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_matches_sofascore_event_id", table_name="matches")
    op.drop_column("matches", "sofascore_event_id")
