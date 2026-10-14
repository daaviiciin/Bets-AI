"""odds_history table

Historial de cuotas (fase 1, bloque 2). Una fila por cambio de precio de
cada línea, con el intervalo ``[first_seen_at, last_seen_at]`` en que estuvo
vigente. No se purga con ``WINAMAX_SNAPSHOT_RETENTION``.

Revision ID: 9d4aa151b886
Revises: d1e2a3b4c5d6
Create Date: 2026-10-14 12:42:32.988108

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9d4aa151b886'
down_revision: Union[str, None] = 'd1e2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('odds_history',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('bookmaker_id', sa.Integer(), nullable=False),
    sa.Column('market_id', sa.Integer(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=True),
    sa.Column('outcome_name', sa.String(length=128), nullable=False),
    sa.Column('outcome_point', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('price', sa.Numeric(precision=8, scale=3), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['bookmaker_id'], ['bookmakers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['market_id'], ['markets.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_odds_history_line', 'odds_history', ['match_id', 'bookmaker_id', 'market_id', 'outcome_name', 'first_seen_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_odds_history_line', table_name='odds_history')
    op.drop_table('odds_history')
