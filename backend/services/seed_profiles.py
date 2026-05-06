"""Seed de los 4 perfiles de usuario por defecto.

Ejecutar tras la migración de la tabla ``user_profiles``:

    python -m backend.services.seed_profiles

Es idempotente: si ya existen perfiles con el mismo nombre, no los duplica.
"""

from __future__ import annotations

import logging
import sys
from decimal import Decimal

from sqlalchemy import select

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.models import UserProfile


DEFAULT_PROFILES: list[dict] = [
    {
        "name": "Conservador",
        "description": (
            "Cuotas medias-bajas con alta probabilidad. Combinadas dobles a 5 €. "
            "Tu estilo: combinar 2 picks con cuotas ~1.50 para sacar 3-4."
        ),
        "risk_profile": "conservative",
        "bankroll": Decimal("100"),
        "odds_min": Decimal("1.40"),
        "odds_max": Decimal("1.75"),
        "min_real_probability": Decimal("0.55"),
        "min_ev": Decimal("0.01"),
        "kelly_modifier": Decimal("0.25"),
        "max_stake_pct": Decimal("0.05"),
        "flat_stake": Decimal("5"),
        "parlay_enabled": True,
        "parlay_legs": 2,
        "parlay_combined_odds_min": Decimal("2.00"),
        "parlay_combined_odds_max": Decimal("4.00"),
        "is_default": True,
    },
    {
        "name": "Inversor",
        "description": (
            "Apuestas individuales a cuota ~2 con stake mayor. "
            "Sin combinadas: prefieres invertir más en un único pick "
            "donde mercado y modelo coinciden."
        ),
        "risk_profile": "investor",
        "bankroll": Decimal("500"),
        "odds_min": Decimal("1.70"),
        "odds_max": Decimal("2.20"),
        "min_real_probability": Decimal("0.45"),
        "min_ev": Decimal("0.01"),
        "kelly_modifier": Decimal("0.5"),
        "max_stake_pct": Decimal("0.10"),
        "flat_stake": Decimal("25"),
        "parlay_enabled": False,
        "parlay_legs": 2,
        "parlay_combined_odds_min": None,
        "parlay_combined_odds_max": None,
        "is_default": False,
    },
    {
        "name": "Equilibrado",
        "description": "Mezcla apuestas seguras y de cuota media. Stake adaptado al valor.",
        "risk_profile": "balanced",
        "bankroll": Decimal("100"),
        "odds_min": Decimal("1.50"),
        "odds_max": Decimal("2.50"),
        "min_real_probability": Decimal("0.50"),
        "min_ev": Decimal("0.02"),
        "kelly_modifier": Decimal("0.5"),
        "max_stake_pct": Decimal("0.07"),
        "flat_stake": None,
        "parlay_enabled": True,
        "parlay_legs": 2,
        "parlay_combined_odds_min": Decimal("2.50"),
        "parlay_combined_odds_max": Decimal("6.00"),
        "is_default": False,
    },
    {
        "name": "Arriesgado",
        "description": "Acepta cuotas medias-altas si hay valor. Kelly puro, mayor varianza.",
        "risk_profile": "aggressive",
        "bankroll": Decimal("100"),
        "odds_min": Decimal("2.00"),
        "odds_max": Decimal("5.00"),
        "min_real_probability": Decimal("0.0"),
        "min_ev": Decimal("0.07"),
        "kelly_modifier": Decimal("1.0"),
        "max_stake_pct": Decimal("0.10"),
        "flat_stake": None,
        "parlay_enabled": False,
        "parlay_legs": 2,
        "parlay_combined_odds_min": None,
        "parlay_combined_odds_max": None,
        "is_default": False,
    },
    {
        "name": "Cazador de premios",
        "description": "Stake bajo a cuotas altas con valor. Estilo lotería responsable.",
        "risk_profile": "lottery",
        "bankroll": Decimal("100"),
        "odds_min": Decimal("3.00"),
        "odds_max": Decimal("20.00"),
        "min_real_probability": Decimal("0.0"),
        "min_ev": Decimal("0.10"),
        "kelly_modifier": Decimal("0.25"),
        "max_stake_pct": Decimal("0.02"),
        "flat_stake": Decimal("2"),
        "parlay_enabled": False,
        "parlay_legs": 3,
        "parlay_combined_odds_min": None,
        "parlay_combined_odds_max": None,
        "is_default": False,
    },
]


def main() -> int:
    force_utf8_stdio()
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    SessionLocal = get_session_factory()
    creados = actualizados = 0

    with SessionLocal() as session:
        for spec in DEFAULT_PROFILES:
            existing = session.scalar(
                select(UserProfile).where(UserProfile.name == spec["name"])
            )
            if existing:
                for key, value in spec.items():
                    setattr(existing, key, value)
                actualizados += 1
                log.info("Perfil actualizado: %s", spec["name"])
            else:
                session.add(UserProfile(**spec))
                creados += 1
                log.info("Perfil creado: %s", spec["name"])
        session.commit()

    log.info("Resumen: %d creados, %d actualizados", creados, actualizados)
    return 0


if __name__ == "__main__":
    sys.exit(main())
