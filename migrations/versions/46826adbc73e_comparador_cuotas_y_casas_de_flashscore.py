"""comparador_cuotas y casas de flashscore

Tabla ``comparador_cuotas`` (cuotas de bet365 y del resto de casas españolas leídas del
comparador de Flashscore) y dos columnas en ``bookmakers``: ``role`` (soft / sharp / exchange)
y ``flashscore_id`` (id de la casa en Flashscore, único).

Revisada a mano: se ha quitado la deriva antigua que trae el autogenerate (UNIQUE y defaults de
``app_users``, ``featured_daily_picks``, ``saved_parlays`` y ``calibration_reports``).

Revision ID: 46826adbc73e
Revises: b0b69754ae07
Create Date: 2026-11-13 11:12:32.051838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '46826adbc73e'
down_revision: Union[str, None] = 'b0b69754ae07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'comparador_cuotas',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('bookmaker_id', sa.Integer(), nullable=False),
        sa.Column('market_id', sa.Integer(), nullable=False),
        sa.Column('outcome_name', sa.String(length=64), nullable=False),
        sa.Column('outcome_point', sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column('price', sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column('opening_price', sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['bookmaker_id'], ['bookmakers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_comparador_cuotas_linea', 'comparador_cuotas',
        ['match_id', 'bookmaker_id', 'market_id', 'outcome_name', 'first_seen_at'], unique=False,
    )
    op.create_index(
        'ix_comparador_cuotas_match_visto', 'comparador_cuotas',
        ['match_id', 'last_seen_at'], unique=False,
    )
    op.add_column('bookmakers', sa.Column('role', sa.String(length=16), nullable=True))
    op.add_column('bookmakers', sa.Column('flashscore_id', sa.Integer(), nullable=True))
    op.create_unique_constraint('uq_bookmakers_flashscore_id', 'bookmakers', ['flashscore_id'])


def downgrade() -> None:
    op.drop_constraint('uq_bookmakers_flashscore_id', 'bookmakers', type_='unique')
    op.drop_column('bookmakers', 'flashscore_id')
    op.drop_column('bookmakers', 'role')
    op.drop_index('ix_comparador_cuotas_match_visto', table_name='comparador_cuotas')
    op.drop_index('ix_comparador_cuotas_linea', table_name='comparador_cuotas')
    op.drop_table('comparador_cuotas')
