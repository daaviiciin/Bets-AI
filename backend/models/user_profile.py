"""Modelo ``UserProfile``: perfil de apostador con bankroll y filtros propios.

Cada perfil define:
* Bankroll y stake
* Filtros sobre las apuestas recomendadas (cuota, EV, prob mínima)
* Política de stake (Kelly fraccional o stake plano)
* Política de combinadas (número de patas, cuota objetivo)
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    risk_profile: Mapped[str] = mapped_column(String(32), nullable=False)

    bankroll: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("100"))

    odds_min: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("1.30"))
    odds_max: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("10.00"))
    min_real_probability: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.0"),
    )
    min_ev: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.05"))

    kelly_modifier: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("0.25"))
    max_stake_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    flat_stake: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    parlay_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parlay_legs: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    parlay_combined_odds_min: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    parlay_combined_odds_max: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<UserProfile id={self.id} name={self.name!r} risk={self.risk_profile}>"
