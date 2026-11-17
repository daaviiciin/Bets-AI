"""combinada del partido en picks del dia

Columna ``match_combo_json`` en ``featured_daily_picks`` (B14f): la "Combinada del partido",
un pick aparte de 2–3 selecciones de un mismo partido (puede llevar props de jugador
experimentales). El trigger de congelado se amplía para cubrirla: una vez guardada, no cambia.

Escrita a mano (sin la deriva antigua de UNIQUE y defaults que trae el autogenerate en
``app_users``, ``featured_daily_picks``, ``saved_parlays`` y ``calibration_reports``).

Revision ID: c7d41e9a2b15
Revises: 46826adbc73e
Create Date: 2026-11-13 11:15:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7d41e9a2b15'
down_revision: Union[str, None] = '46826adbc73e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ``json`` no tiene operador de igualdad en PostgreSQL: se compara como ``jsonb``.
_FUNCION_CON_PARTIDO = """
CREATE OR REPLACE FUNCTION featured_picks_congelados() RETURNS trigger AS $$
BEGIN
    IF NEW.pick_date IS DISTINCT FROM OLD.pick_date
       OR NEW.picks_json::jsonb IS DISTINCT FROM OLD.picks_json::jsonb
       OR NEW.combo_json::jsonb IS DISTINCT FROM OLD.combo_json::jsonb
       OR NEW.match_combo_json::jsonb IS DISTINCT FROM OLD.match_combo_json::jsonb
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

# La de ``3e547a4eef99`` (sin la combinada del partido), para el downgrade.
_FUNCION_ANTERIOR = """
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


def upgrade() -> None:
    op.add_column('featured_daily_picks', sa.Column('match_combo_json', sa.JSON(), nullable=True))
    op.execute(_FUNCION_CON_PARTIDO)


def downgrade() -> None:
    op.execute(_FUNCION_ANTERIOR)
    op.drop_column('featured_daily_picks', 'match_combo_json')
