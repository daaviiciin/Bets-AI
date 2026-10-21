"""Tests de los endurecimientos de seguridad sobre entradas y mensajes de error."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.schemas.analysis import CustomBetAssessmentRequest
from backend.app.schemas.responses import MAX_SAVED_PARLAY_LEGS, SavedParlayCreateIn
from backend.scrapers.odds_api import ocultar_api_key
from backend.services.busqueda_texto import patron_like_contiene


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


class TestOcultarApiKey:
    def test_enmascara_apikey_en_url_de_excepcion(self) -> None:
        msg = (
            "HTTPSConnectionPool(host='api.the-odds-api.com', port=443): Max retries "
            "exceeded with url: /v4/sports/x/odds?apiKey=abcd1234secret&regions=eu"
        )
        limpio = ocultar_api_key(msg)
        assert "abcd1234secret" not in limpio
        assert "apiKey=***&regions=eu" in limpio

    def test_enmascara_la_clave_literal_aunque_no_vaya_en_query(self) -> None:
        assert ocultar_api_key("clave abcd1234secret inválida", "abcd1234secret") == (
            "clave *** inválida"
        )

    def test_texto_sin_clave_queda_igual(self) -> None:
        assert ocultar_api_key("timeout de red", "") == "timeout de red"


class TestPatronLikeContiene:
    def test_texto_normal_va_entre_comodines_y_en_minusculas(self) -> None:
        assert patron_like_contiene("Pedri") == "%pedri%"

    def test_porcentaje_y_guion_bajo_se_escapan(self) -> None:
        assert patron_like_contiene("%_") == "%\\%\\_%"

    def test_barra_invertida_se_escapa(self) -> None:
        assert patron_like_contiene("a\\b") == "%a\\\\b%"


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


class TestLimitesApuestaManual:
    def test_cuota_desorbitada_se_rechaza(self) -> None:
        with pytest.raises(ValidationError):
            CustomBetAssessmentRequest.model_validate(
                {"legs": [{"label": "x", "decimal_odds": "1e400"}]}
            )

    def test_cuota_normal_es_valida(self) -> None:
        req = CustomBetAssessmentRequest.model_validate(
            {"legs": [{"label": "x", "decimal_odds": "1.85", "market": "h2h"}]}
        )
        assert str(req.legs[0].decimal_odds) == "1.85"
