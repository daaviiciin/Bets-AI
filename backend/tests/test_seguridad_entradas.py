"""Tests de los endurecimientos de seguridad sobre entradas y mensajes de error."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.schemas.responses import MAX_SAVED_PARLAY_LEGS, SavedParlayCreateIn


def _pata(**extra: object) -> dict:
    base = {
        "match_id": 1,
        "home_team": "Betis",
        "away_team": "Sevilla",
        "market_key": "h2h",
        "outcome_name": "Betis",
        "offered_odds": "2.10",
    }
    base.update(extra)
    return base


class TestLimitesCombinadaGuardada:
    def test_combinada_normal_es_valida(self) -> None:
        body = SavedParlayCreateIn.model_validate({"legs": [_pata()]})
        assert len(body.legs) == 1

    def test_demasiadas_patas_se_rechaza(self) -> None:
        with pytest.raises(ValidationError):
            SavedParlayCreateIn.model_validate(
                {"legs": [_pata() for _ in range(MAX_SAVED_PARLAY_LEGS + 1)]}
            )

    def test_cuota_desorbitada_se_rechaza(self) -> None:
        with pytest.raises(ValidationError):
            SavedParlayCreateIn.model_validate({"legs": [_pata(offered_odds="1e400")]})

    def test_cuota_menor_o_igual_a_1_se_rechaza(self) -> None:
        with pytest.raises(ValidationError):
            SavedParlayCreateIn.model_validate({"legs": [_pata(offered_odds="1")]})

    def test_nombre_de_equipo_gigante_se_rechaza(self) -> None:
        with pytest.raises(ValidationError):
            SavedParlayCreateIn.model_validate({"legs": [_pata(home_team="x" * 5000)]})
