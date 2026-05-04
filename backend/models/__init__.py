"""Modelos SQLAlchemy del proyecto.

Importar todos los modelos aquí garantiza que ``Base.metadata`` los registre
antes de que Alembic genere migraciones.
"""

from backend.models.base import Base, TimestampMixin
from backend.models.bookmaker import Bookmaker
from backend.models.league import League
from backend.models.market import Market
from backend.models.match import Match
from backend.models.odds import Odds
from backend.models.snapshot import Snapshot
from backend.models.team import Team

__all__ = [
    "Base",
    "TimestampMixin",
    "Bookmaker",
    "League",
    "Market",
    "Match",
    "Odds",
    "Snapshot",
    "Team",
]
