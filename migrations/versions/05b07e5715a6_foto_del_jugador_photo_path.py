"""foto del jugador (photo_path)

Revision ID: 05b07e5715a6
Revises: e4e49e99c5f9
Create Date: 2026-10-19 19:56:03.058893

``players.photo_path``: fichero de la foto en el CDN de imágenes de Flashscore (llega con
``epmsse``). Solo lo de esta revisión: el autogenerate volvió a detectar las diferencias
antiguas conocidas (``server_default`` de ``saved_parlays``/``app_users``/
``featured_daily_picks``/``calibration_reports`` y restricciones únicas convertidas en
índices), que no se tocan aquí.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '05b07e5715a6'
down_revision: Union[str, None] = 'e4e49e99c5f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('players', sa.Column('photo_path', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('players', 'photo_path')
