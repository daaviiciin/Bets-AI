"""versiones del modelo v2

Revision ID: 892f3f9f06c4
Revises: 3e547a4eef99
Create Date: 2026-10-25 19:31:53.932945

Solo la tabla nueva ``model_versions``. Autogenerate proponía además quitar ``server_default`` y
cambiar restricciones únicas de otras tablas (deriva antigua entre modelos y BD); se deja fuera a
propósito, como en ``333abb2b3059`` y ``3e547a4eef99``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '892f3f9f06c4'
down_revision: Union[str, None] = '3e547a4eef99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'model_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tag', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('hyperparams', sa.JSON(), nullable=False),
        sa.Column('calibration', sa.JSON(), nullable=False),
        sa.Column('market_weight', sa.Float(), nullable=False),
        sa.Column('enabled_families', sa.JSON(), nullable=False),
        sa.Column('backtest_metrics', sa.JSON(), nullable=False),
        sa.Column('promoted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tag'),
    )


def downgrade() -> None:
    op.drop_table('model_versions')
