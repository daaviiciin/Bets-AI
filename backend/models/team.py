"""Modelo ``Team``: un equipo de fútbol.

Los equipos son globales: un mismo equipo (Real Madrid) juega múltiples
competiciones (LaLiga, Champions...), por eso no atamos Team a una sola League.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.match import Match


class Team(Base, TimestampMixin):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("external_source", "external_id", name="uq_team_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    external_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ID del equipo en Flashscore (``PX``/``PY`` en el calendario, ``TI`` en la
    # clasificación). Permite enlazar por id en vez de por nombre.
    flashscore_id: Mapped[str | None] = mapped_column(
        String(16), unique=True, nullable=True, index=True,
    )
    # Escudo de Flashscore (``OA``/``OB`` del calendario) y sus dos colores dominantes
    # (``#rrggbb``), calculados una vez a partir de la imagen (``services/team_crests.py``).
    logo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    secondary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    home_matches: Mapped[list["Match"]] = relationship(
        back_populates="home_team",
        foreign_keys="Match.home_team_id",
    )
    away_matches: Mapped[list["Match"]] = relationship(
        back_populates="away_team",
        foreign_keys="Match.away_team_id",
    )

    def __repr__(self) -> str:
        return f"<Team id={self.id} name={self.name!r}>"
