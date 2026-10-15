"""Tests de la agregación de temporada por jugador y helpers relacionados (sin BD)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.services.flashscore_player_stats_ingester import position_code
from backend.services.player_season_rollup import (
    PlayerMatchLine,
    SeasonTotals,
    aggregate_season,
    season_start_year,
)
from backend.services.team_name_matching import player_name_key


def _line(day: int, *, minutes: int, starter: bool = True, team: int = 1, shots: int = 0,
          sot: int = 0, goals: int = 0, xg: str = "0") -> PlayerMatchLine:
    return PlayerMatchLine(
        team_id=team,
        commence_time=datetime(2026, 10, day, 18, 0, tzinfo=timezone.utc),
        is_starter=starter,
        minutes=minutes,
        goals=goals,
        assists=0,
        shots=shots,
        shots_on_target=sot,
        xg=Decimal(xg),
        yellow_cards=0,
        red_cards=0,
    )


class TestAggregateSeason:
    def test_suma_partidos_titularidades_y_minutos(self):
        t = aggregate_season([
            _line(1, minutes=90, shots=3, sot=2, goals=1, xg="0.45"),
            _line(8, minutes=20, starter=False, shots=1, sot=0, xg="0.10"),
        ])
        assert (t.matches_played, t.starts, t.minutes) == (2, 1, 110)
        assert (t.shots, t.shots_on_target, t.goals) == (4, 2, 1)
        assert t.xg == Decimal("0.55")

    def test_partidos_sin_minutos_no_cuentan(self):
        t = aggregate_season([_line(1, minutes=90), _line(8, minutes=0, starter=False)])
        assert t.matches_played == 1

    def test_sin_minutos_devuelve_none(self):
        assert aggregate_season([_line(1, minutes=0, starter=False)]) is None

    def test_equipo_del_partido_mas_reciente(self):
        t = aggregate_season([_line(1, minutes=90, team=1), _line(8, minutes=90, team=2)])
        assert t.team_id == 2

    def test_metricas_por_90(self):
        t = aggregate_season([_line(1, minutes=90, shots=3), _line(8, minutes=90, shots=1)])
        assert t.nineties == Decimal("2.0")
        assert t.per_90(t.shots) == Decimal("2.00")

    def test_ratio_con_denominador_cero(self):
        assert SeasonTotals.ratio(1, 0) is None


class TestSeasonStartYear:
    def test_otono_es_la_temporada_que_empieza(self):
        assert season_start_year(datetime(2026, 10, 14, tzinfo=timezone.utc)) == 2026

    def test_primavera_es_la_temporada_anterior(self):
        assert season_start_year(datetime(2027, 3, 1, tzinfo=timezone.utc)) == 2026


class TestPositionCode:
    def test_posiciones_de_flashscore(self):
        assert position_code("Guardameta", is_goalkeeper=True) == "G"
        assert position_code("Defensa central", is_goalkeeper=False) == "D"
        assert position_code("Centrocampista", is_goalkeeper=False) == "M"
        assert position_code("Extremo", is_goalkeeper=False) == "F"

    def test_desconocida(self):
        assert position_code(None, is_goalkeeper=False) is None


class TestPlayerNameKey:
    def test_orden_flashscore_y_sofascore_coinciden(self):
        assert player_name_key("Gordon Anthony") == player_name_key("Anthony Gordon")
        assert player_name_key("Olmo Dani") == player_name_key("Dani Olmo")

    def test_tildes_y_puntuacion(self):
        assert player_name_key("Kounde Jules") == player_name_key("Jules Koundé")
        assert player_name_key("Ter Stegen Marc-André") == player_name_key("Marc-André ter Stegen")

    def test_jugadores_distintos(self):
        assert player_name_key("García Joan") != player_name_key("García Eric")
