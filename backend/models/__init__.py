"""Modelos SQLAlchemy del proyecto.

Importar todos los modelos aquí garantiza que ``Base.metadata`` los registre
antes de que Alembic genere migraciones.
"""

from backend.models.base import Base, TimestampMixin
from backend.models.bookmaker import Bookmaker
from backend.models.calibration_report import CalibrationReport
from backend.models.league import League
from backend.models.market import Market
from backend.models.match import Match
from backend.models.match_stats import MatchStats
from backend.models.odds import Odds
from backend.models.player import Player
from backend.models.player_match_stats import PlayerMatchStats
from backend.models.player_stats import PlayerSeasonStats
from backend.models.season import Season
from backend.models.snapshot import Snapshot
from backend.models.team import Team
from backend.models.team_stats import TeamSeasonStats
from backend.models.user_profile import UserProfile

__all__ = [
    "Base",
    "TimestampMixin",
    "Bookmaker",
    "CalibrationReport",
    "League",
    "Market",
    "Match",
    "MatchStats",
    "Odds",
    "Player",
    "PlayerMatchStats",
    "PlayerSeasonStats",
    "Season",
    "Snapshot",
    "Team",
    "TeamSeasonStats",
    "UserProfile",
]
