"""calibration_report_dixon_coles_rho

Revision ID: 1e4e184fb1f2
Revises: b97ce7cf2e65
Create Date: 2026-05-05 14:48:26.011637

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e4e184fb1f2'
down_revision: Union[str, None] = 'b97ce7cf2e65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calibration_reports",
        sa.Column(
            "dixon_coles_rho",
            sa.Numeric(precision=6, scale=4),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("calibration_reports", "dixon_coles_rho")
