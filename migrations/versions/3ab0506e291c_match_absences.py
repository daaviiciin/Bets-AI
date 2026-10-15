"""match absences

Bajas y dudas por partido (fase 1, bloque 1), desde Flashscore. Añade la tabla
``match_absences`` y ``matches.absences_fetched_at`` (última consulta).

Revision ID: 3ab0506e291c
Revises: 9d4aa151b886
Create Date: 2026-10-14 13:32:29.407239

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3ab0506e291c'
down_revision: Union[str, None] = '9d4aa151b886'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('match_absences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('match_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('player_external_id', sa.String(length=32), nullable=False),
    sa.Column('player_name', sa.String(length=128), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=128), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('match_id', 'player_external_id', name='uq_match_absence_player')
    )
    op.create_index(op.f('ix_match_absences_match_id'), 'match_absences', ['match_id'], unique=False)
    op.create_index(op.f('ix_match_absences_team_id'), 'match_absences', ['team_id'], unique=False)
    op.add_column('matches', sa.Column('absences_fetched_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('matches', 'absences_fetched_at')
    op.drop_index(op.f('ix_match_absences_team_id'), table_name='match_absences')
    op.drop_index(op.f('ix_match_absences_match_id'), table_name='match_absences')
    op.drop_table('match_absences')
