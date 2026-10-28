"""Tests de lo que falta descargar al pulsar "Comprobar" (``resultados_bajo_demanda``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.models.match import MATCH_STATUS_FINISHED, MATCH_STATUS_SCHEDULED
from backend.services.resultados_bajo_demanda import dias_hacia_atras, que_falta

AHORA = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


def _partido(
    *,
    hace: timedelta,
    status: str = MATCH_STATUS_SCHEDULED,
    external_id: str = "flashscore:8MQeSuQE",
    stats_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        external_id=external_id,
        commence_time=AHORA - hace,
        status=status,
        player_stats_fetched_at=stats_at,
    )


class TestQueFalta:
    def test_partido_de_anoche_sin_marcador_pide_resultado(self):
        m = _partido(hace=timedelta(hours=12))
        assert que_falta([m], AHORA).sin_resultado == (m,)

    def test_partido_en_juego_no_pide_nada(self):
        assert que_falta([_partido(hace=timedelta(minutes=80))], AHORA).vacio

    def test_partido_sin_id_de_flashscore_se_ignora(self):
        m = _partido(hace=timedelta(hours=12), external_id="odds_api:abc")
        assert que_falta([m], AHORA).vacio

    def test_terminado_sin_stats_de_jugador_pide_stats(self):
        m = _partido(hace=timedelta(hours=12), status=MATCH_STATUS_FINISHED)
        falta = que_falta([m], AHORA)
        assert falta.sin_stats == (m,)
        assert falta.sin_resultado == ()

    def test_terminado_con_stats_no_pide_nada(self):
        m = _partido(hace=timedelta(hours=12), status=MATCH_STATUS_FINISHED, stats_at=AHORA)
        assert que_falta([m], AHORA).vacio

    def test_terminado_hace_una_semana_sin_stats_lo_deja_al_refresh(self):
        m = _partido(hace=timedelta(days=7), status=MATCH_STATUS_FINISHED)
        assert que_falta([m], AHORA).vacio

    def test_fecha_sin_zona_horaria_se_toma_como_utc(self):
        m = _partido(hace=timedelta(hours=12))
        m.commence_time = m.commence_time.replace(tzinfo=None)
        assert que_falta([m], AHORA).sin_resultado == (m,)


class TestDiasHaciaAtras:
    def test_partido_de_anoche_pide_hoy_y_ayer(self):
        assert dias_hacia_atras([_partido(hace=timedelta(hours=12))], AHORA) == 1

    def test_cubre_el_partido_mas_antiguo(self):
        ms = [_partido(hace=timedelta(hours=12)), _partido(hace=timedelta(days=3, hours=1))]
        assert dias_hacia_atras(ms, AHORA) == 4

    def test_no_pasa_del_maximo_del_feed(self):
        assert dias_hacia_atras([_partido(hace=timedelta(days=30))], AHORA) == 7
