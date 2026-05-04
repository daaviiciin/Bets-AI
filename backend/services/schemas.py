"""Schemas Pydantic para parsear y validar el JSON de The Odds API.

Hacemos una capa fina de validación antes de tocar la BD: si el JSON viene
malformado, fallamos rápido con un mensaje claro en vez de explotar a mitad de
inserción y dejar la transacción a medias.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Outcome(BaseModel):
    """Una opción de un mercado (p.ej. 'Real Madrid' a 1.55, o 'Over 2.5' a 1.85)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    price: Decimal
    point: Decimal | None = None

    @field_validator("price")
    @classmethod
    def _price_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError(f"price debe ser > 0 (recibido: {value})")
        return value


class MarketEntry(BaseModel):
    """Un mercado de un bookmaker (h2h, totals, etc.)."""

    key: str
    last_update: datetime
    outcomes: list[Outcome]


class BookmakerEntry(BaseModel):
    """Un bookmaker con sus mercados para un evento."""

    key: str
    title: str
    last_update: datetime
    markets: list[MarketEntry]


class EventEntry(BaseModel):
    """Un partido (evento) con todos los bookmakers que ofrecen cuotas."""

    id: str
    sport_key: str
    sport_title: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[BookmakerEntry] = Field(default_factory=list)


class SnapshotMetadata(BaseModel):
    """Metadata que añade nuestro scraper al envolver la respuesta de la API."""

    liga: str
    sport_key: str
    regions: str
    markets: str
    captured_at: datetime
    requests_remaining: int | None = None
    requests_used: int | None = None


class SnapshotFile(BaseModel):
    """Estructura completa de uno de nuestros ficheros ``data/raw/*.json``."""

    metadata: SnapshotMetadata
    events: list[EventEntry]
