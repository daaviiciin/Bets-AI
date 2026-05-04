"""Tests de los schemas Pydantic del procesador."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.services.schemas import (
    BookmakerEntry,
    EventEntry,
    Outcome,
    SnapshotFile,
)


def test_outcome_acepta_valores_validos():
    o = Outcome.model_validate({"name": "Real Madrid", "price": 1.55})
    assert o.name == "Real Madrid"
    assert o.price == Decimal("1.55")
    assert o.point is None


def test_outcome_acepta_punto_para_totals():
    o = Outcome.model_validate({"name": "Over", "price": 1.85, "point": 2.5})
    assert o.point == Decimal("2.5")


def test_outcome_rechaza_precio_negativo():
    with pytest.raises(ValidationError, match="price debe ser > 0"):
        Outcome.model_validate({"name": "Real Madrid", "price": -1.5})


def test_outcome_rechaza_precio_cero():
    with pytest.raises(ValidationError, match="price debe ser > 0"):
        Outcome.model_validate({"name": "Real Madrid", "price": 0})


def test_event_acepta_lista_vacia_de_bookmakers():
    raw = {
        "id": "abc",
        "sport_key": "soccer_spain_la_liga",
        "sport_title": "La Liga",
        "commence_time": "2026-05-17T17:00:00Z",
        "home_team": "Barcelona",
        "away_team": "Real Betis",
    }
    event = EventEntry.model_validate(raw)
    assert event.bookmakers == []


def test_snapshot_file_completo_se_parsea_ok():
    raw = {
        "metadata": {
            "liga": "champions_league",
            "sport_key": "soccer_uefa_champs_league",
            "regions": "eu",
            "markets": "h2h,totals",
            "captured_at": "2026-05-04T09:47:07.478854+00:00",
            "requests_remaining": 487,
            "requests_used": 13,
        },
        "events": [
            {
                "id": "f3df07dd",
                "sport_key": "soccer_uefa_champs_league",
                "sport_title": "UEFA Champions League",
                "commence_time": "2026-05-05T19:00:00Z",
                "home_team": "Arsenal",
                "away_team": "Atlético Madrid",
                "bookmakers": [
                    {
                        "key": "winamax_fr",
                        "title": "Winamax (FR)",
                        "last_update": "2026-05-04T09:46:47Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "last_update": "2026-05-04T09:46:47Z",
                                "outcomes": [
                                    {"name": "Arsenal", "price": 1.68},
                                    {"name": "Atlético Madrid", "price": 5.4},
                                    {"name": "Draw", "price": 4.0},
                                ],
                            },
                            {
                                "key": "totals",
                                "last_update": "2026-05-04T09:46:47Z",
                                "outcomes": [
                                    {"name": "Over", "price": 1.97, "point": 2.5},
                                    {"name": "Under", "price": 1.85, "point": 2.5},
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }
    snap = SnapshotFile.model_validate(raw)
    assert snap.metadata.liga == "champions_league"
    assert snap.metadata.requests_remaining == 487
    assert len(snap.events) == 1

    event = snap.events[0]
    assert event.home_team == "Arsenal"
    assert len(event.bookmakers) == 1

    bm: BookmakerEntry = event.bookmakers[0]
    assert bm.key == "winamax_fr"
    assert len(bm.markets) == 2

    h2h = next(m for m in bm.markets if m.key == "h2h")
    assert len(h2h.outcomes) == 3
    assert {o.name for o in h2h.outcomes} == {"Arsenal", "Atlético Madrid", "Draw"}

    totals = next(m for m in bm.markets if m.key == "totals")
    assert all(o.point == Decimal("2.5") for o in totals.outcomes)
