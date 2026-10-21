"""importe y resultado manual en saved_parlays

Revision ID: 333abb2b3059
Revises: 05b07e5715a6
Create Date: 2026-10-21 12:44:21.806149

Solo las dos columnas nuevas. Autogenerate proponía además quitar ``server_default`` y cambiar
restricciones únicas de otras tablas (deriva antigua entre modelos y BD); se deja fuera a propósito.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '333abb2b3059'
down_revision: Union[str, None] = '05b07e5715a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('saved_parlays', sa.Column('manual_result', sa.String(length=24), nullable=True))
    op.add_column('saved_parlays', sa.Column('stake', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('saved_parlays', 'stake')
    op.drop_column('saved_parlays', 'manual_result')
