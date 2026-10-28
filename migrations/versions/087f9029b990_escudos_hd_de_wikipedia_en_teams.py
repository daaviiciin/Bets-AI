"""escudos HD de Wikipedia en teams

Solo las dos columnas de ``teams``. El autogenerate también proponía quitar server_default
y cambiar restricciones únicas de otras tablas (desfase previo, ajeno a esto): se omite.

Revision ID: 087f9029b990
Revises: 892f3f9f06c4
Create Date: 2026-10-28 10:47:04.347187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '087f9029b990'
down_revision: Union[str, None] = '892f3f9f06c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('teams', sa.Column('logo_hd_url', sa.String(length=512), nullable=True))
    op.add_column('teams', sa.Column('logo_hd_checked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('teams', 'logo_hd_checked_at')
    op.drop_column('teams', 'logo_hd_url')
