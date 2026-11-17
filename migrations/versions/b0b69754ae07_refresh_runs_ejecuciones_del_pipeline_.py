"""refresh_runs: ejecuciones del pipeline completo

Solo crea la tabla. El autogenerate proponía además quitar restricciones UNIQUE y server
defaults de app_users, calibration_reports, featured_daily_picks y saved_parlays: es deriva
antigua entre modelos y BD, ajena a este cambio, y se ha quitado a mano.

Revision ID: b0b69754ae07
Revises: 087f9029b990
Create Date: 2026-11-05 10:34:35.488523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b0b69754ae07'
down_revision: Union[str, None] = '087f9029b990'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('refresh_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('origen', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ok', sa.Boolean(), nullable=True),
    sa.Column('errores', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refresh_runs_started_at'), 'refresh_runs', ['started_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_refresh_runs_started_at'), table_name='refresh_runs')
    op.drop_table('refresh_runs')
