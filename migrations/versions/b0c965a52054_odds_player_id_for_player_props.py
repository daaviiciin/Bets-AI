"""odds_player_id_for_player_props

Revision ID: b0c965a52054
Revises: 20940d53e9b2
Create Date: 2026-05-05 16:35:38.196244

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0c965a52054'
down_revision: Union[str, None] = '20940d53e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "odds",
        sa.Column("player_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_odds_player_id",
        "odds",
        "players",
        ["player_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_odds_player", "odds", ["player_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_odds_player", table_name="odds")
    op.drop_constraint("fk_odds_player_id", "odds", type_="foreignkey")
    op.drop_column("odds", "player_id")
