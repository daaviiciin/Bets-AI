"""flashscore ids (teams, players) y control de stats de jugadores

Sustitución de Sofascore por Flashscore:

- ``teams.flashscore_id`` y ``players.flashscore_id`` (únicos): enlazar por id
  en vez de por nombre.
- ``matches.player_stats_fetched_at``: última consulta de stats de jugadores;
  evita repetir partidos para los que Flashscore no publica estadísticas.

Revision ID: af9813f29f47
Revises: 3ab0506e291c
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'af9813f29f47'
down_revision: Union[str, None] = '3ab0506e291c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('teams', sa.Column('flashscore_id', sa.String(length=16), nullable=True))
    op.create_index(op.f('ix_teams_flashscore_id'), 'teams', ['flashscore_id'], unique=True)
    op.add_column('players', sa.Column('flashscore_id', sa.String(length=16), nullable=True))
    op.create_index(op.f('ix_players_flashscore_id'), 'players', ['flashscore_id'], unique=True)
    op.add_column(
        'matches',
        sa.Column('player_stats_fetched_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('matches', 'player_stats_fetched_at')
    op.drop_index(op.f('ix_players_flashscore_id'), table_name='players')
    op.drop_column('players', 'flashscore_id')
    op.drop_index(op.f('ix_teams_flashscore_id'), table_name='teams')
    op.drop_column('teams', 'flashscore_id')
