"""escudos y colores de equipo + alineaciones oficiales

Revision ID: e4e49e99c5f9
Revises: af9813f29f47
Create Date: 2026-10-16 13:55:03.588892

Solo lo de esta revisión. El autogenerate detectó además diferencias antiguas entre
modelos y BD (``server_default`` de ``saved_parlays``/``app_users``/``featured_daily_picks``/
``calibration_reports`` y restricciones únicas convertidas en índices) que no son de este
cambio y no se tocan aquí.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e4e49e99c5f9'
down_revision: Union[str, None] = 'af9813f29f47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'match_lineups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('is_home', sa.Boolean(), nullable=False),
        sa.Column('player_external_id', sa.String(length=32), nullable=False),
        sa.Column('player_name', sa.String(length=128), nullable=False),
        sa.Column('position', sa.String(length=8), nullable=True),
        sa.Column('is_starter', sa.Boolean(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('match_id', 'player_external_id', name='uq_match_lineup_player'),
    )
    op.create_index(op.f('ix_match_lineups_match_id'), 'match_lineups', ['match_id'], unique=False)
    op.add_column('teams', sa.Column('logo_url', sa.String(length=255), nullable=True))
    op.add_column('teams', sa.Column('primary_color', sa.String(length=7), nullable=True))
    op.add_column('teams', sa.Column('secondary_color', sa.String(length=7), nullable=True))


def downgrade() -> None:
    op.drop_column('teams', 'secondary_color')
    op.drop_column('teams', 'primary_color')
    op.drop_column('teams', 'logo_url')
    op.drop_index(op.f('ix_match_lineups_match_id'), table_name='match_lineups')
    op.drop_table('match_lineups')
