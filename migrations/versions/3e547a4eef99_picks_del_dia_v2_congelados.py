"""picks del dia v2 congelados

Revision ID: 3e547a4eef99
Revises: 333abb2b3059
Create Date: 2026-10-25 18:46:09.955758

Columnas nuevas de ``featured_daily_picks`` (combinada, versión de reglas y de modelo, hora de
generación, motivos y hash) y un trigger que impide modificar los picks ya guardados: solo puede
cambiar su corrección (``grading_snapshot``, ``last_graded_at``). Las filas existentes quedan
como ``rules_version='v1'``.

Autogenerate proponía además quitar ``server_default`` y cambiar restricciones únicas de otras
tablas (deriva antigua entre modelos y BD); se deja fuera a propósito, como en ``333abb2b3059``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3e547a4eef99'
down_revision: Union[str, None] = '333abb2b3059'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ``json`` no tiene operador de igualdad en PostgreSQL: se compara como ``jsonb``.
_FUNCION_CONGELAR = """
CREATE OR REPLACE FUNCTION featured_picks_congelados() RETURNS trigger AS $$
BEGIN
    IF NEW.pick_date IS DISTINCT FROM OLD.pick_date
       OR NEW.picks_json::jsonb IS DISTINCT FROM OLD.picks_json::jsonb
       OR NEW.combo_json::jsonb IS DISTINCT FROM OLD.combo_json::jsonb
       OR NEW.picks_sha256 IS DISTINCT FROM OLD.picks_sha256
       OR NEW.rules_version IS DISTINCT FROM OLD.rules_version
       OR NEW.model_version IS DISTINCT FROM OLD.model_version
       OR NEW.generated_at IS DISTINCT FROM OLD.generated_at THEN
        RAISE EXCEPTION 'Los picks del % están congelados: solo puede cambiar su corrección',
            OLD.pick_date;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER featured_picks_congelados
BEFORE UPDATE ON featured_daily_picks
FOR EACH ROW EXECUTE FUNCTION featured_picks_congelados();
"""


def upgrade() -> None:
    op.add_column('featured_daily_picks', sa.Column('combo_json', sa.JSON(), nullable=True))
    op.add_column(
        'featured_daily_picks',
        sa.Column('rules_version', sa.String(length=8), server_default='v1', nullable=False),
    )
    op.add_column('featured_daily_picks', sa.Column('model_version', sa.String(length=32), nullable=True))
    op.add_column('featured_daily_picks', sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('featured_daily_picks', sa.Column('selection_meta', sa.JSON(), nullable=True))
    op.add_column('featured_daily_picks', sa.Column('picks_sha256', sa.String(length=64), nullable=True))
    op.execute(_FUNCION_CONGELAR)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS featured_picks_congelados ON featured_daily_picks")
    op.execute("DROP FUNCTION IF EXISTS featured_picks_congelados()")
    op.drop_column('featured_daily_picks', 'picks_sha256')
    op.drop_column('featured_daily_picks', 'selection_meta')
    op.drop_column('featured_daily_picks', 'generated_at')
    op.drop_column('featured_daily_picks', 'model_version')
    op.drop_column('featured_daily_picks', 'rules_version')
    op.drop_column('featured_daily_picks', 'combo_json')
