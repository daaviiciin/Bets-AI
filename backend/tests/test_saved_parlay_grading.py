"""Tests de la liquidación automática de patas (``saved_parlay_grading``), sin BD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.models.match import (
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_FINISHED,
    MATCH_STATUS_POSTPONED,
    MATCH_STATUS_SCHEDULED,
)
from backend.services.saved_parlay_grading import (
    HORAS_ESPERA_STATS,
    StatsEquipo,
    StatsJugador,
    StatsPartido,
    cuota_efectiva,
    grade_single_leg,
    overall_from_leg_results,
)

SAQUE = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
JUSTO_DESPUES = SAQUE + timedelta(hours=3)


def partido(hs: int | None = 2, aw: int | None = 1, *, status: str = MATCH_STATUS_FINISHED):
    """Partido Tottenham - Coventry City con el marcador dado (objeto simple, sin BD)."""
    return SimpleNamespace(
        id=1,
        status=status,
        home_score=hs,
        away_score=aw,
        commence_time=SAQUE,
        home_team=SimpleNamespace(name="Tottenham"),
        away_team=SimpleNamespace(name="Coventry City"),
    )


def pata(market_key: str, outcome_name: str, point: str | None = None, **extra) -> dict:
    return {
        "match_id": 1,
        "home_team": "Tottenham",
        "away_team": "Coventry City",
        "market_key": market_key,
        "outcome_name": outcome_name,
        "outcome_point": point,
        "offered_odds": "1.50",
        **extra,
    }


def resultado(leg: dict, match=None, stats: StatsPartido | None = None, now=JUSTO_DESPUES) -> str:
    return grade_single_leg(leg, match if match is not None else partido(), stats, now=now)["result"]


# Local 2 (1+1), visitante 1 (0+1); tiros a puerta 6 y 3.
STATS = StatsPartido(
    local=StatsEquipo(goals_1h=1, goals_2h=1, shots_on_target=6),
    visitante=StatsEquipo(goals_1h=0, goals_2h=1, shots_on_target=3),
)


class TestDobleOportunidad:
    def test_1x_acierta_si_gana_el_local(self) -> None:
        assert resultado(pata("double_chance", "1X")) == "hit"

    def test_x2_falla_si_gana_el_local(self) -> None:
        assert resultado(pata("double_chance", "X2")) == "miss"

    def test_12_acierta_si_no_hay_empate(self) -> None:
        assert resultado(pata("double_chance", "12")) == "hit"

    def test_12_falla_con_empate(self) -> None:
        assert resultado(pata("double_chance", "12"), partido(1, 1)) == "miss"

    def test_nombres_guardados_en_bd(self) -> None:
        # Forma en que el ingester guarda los outcomes de Winamax.
        assert resultado(pata("double_chance", "Tottenham or Draw")) == "hit"
        assert resultado(pata("double_chance", "Draw or Coventry City")) == "miss"
        assert resultado(pata("double_chance", "Tottenham or Coventry City")) == "hit"

    def test_empate_o_visitante_con_etiqueta_espanola(self) -> None:
        assert resultado(pata("match_double_chance", "Empate o Coventry City"), partido(0, 0)) == "hit"

    def test_etiqueta_desconocida_va_a_revisar(self) -> None:
        assert resultado(pata("double_chance", "Arsenal or Draw")) == "needs_review"


class TestPrimeraYSegundaMitad:
    def test_mas_de_0_5_goles_en_la_primera_parte(self) -> None:
        leg = pata("match_first_half_totals", "Over", "0.5")
        assert resultado(leg, stats=STATS) == "hit"

    def test_menos_de_1_5_goles_en_la_segunda_parte_falla_con_2(self) -> None:
        leg = pata("match_second_half_totals", "Under", "1.5")
        assert resultado(leg, stats=STATS) == "miss"

    def test_goles_del_visitante_en_la_primera_parte(self) -> None:
        leg = pata("team_away_first_half_totals_goals", "Under", "0.5")
        assert resultado(leg, stats=STATS) == "hit"

    def test_goles_del_local_en_la_segunda_parte(self) -> None:
        leg = pata("team_home_second_half_totals_goals", "Over", "0.5")
        assert resultado(leg, stats=STATS) == "hit"

    def test_mitades_que_no_suman_el_marcador_van_a_revisar(self) -> None:
        # Posible prórroga (o datos malos): 1+1 del local no da los 3 del marcador.
        leg = pata("h2h", "Tottenham")
        assert resultado(leg, partido(3, 1), stats=STATS) == "needs_review"


class TestMitadConMasGoles:
    # STATS: 1 gol en la 1ª parte (1+0) y 2 en la 2ª (1+1).
    def test_segunda_mitad_acierta_con_mas_goles_tras_el_descanso(self) -> None:
        assert resultado(pata("match_highest_scoring_half", "2ª mitad"), stats=STATS) == "hit"

    def test_primera_mitad_e_igual_fallan(self) -> None:
        assert resultado(pata("match_highest_scoring_half", "1ª mitad"), stats=STATS) == "miss"
        assert resultado(pata("match_highest_scoring_half", "Igual"), stats=STATS) == "miss"

    def test_igual_acierta_con_los_mismos_goles_en_cada_mitad(self) -> None:
        igualadas = StatsPartido(
            local=StatsEquipo(goals_1h=1, goals_2h=0),
            visitante=StatsEquipo(goals_1h=0, goals_2h=1),
        )
        assert resultado(
            pata("match_highest_scoring_half", "Igual"), partido(1, 1), stats=igualadas,
        ) == "hit"

    def test_sin_goles_por_mitad_espera_las_stats(self) -> None:
        assert resultado(pata("match_highest_scoring_half", "2ª mitad")) == "pending_stats"

    def test_etiqueta_desconocida_va_a_revisar(self) -> None:
        assert resultado(pata("match_highest_scoring_half", "Ninguna"), stats=STATS) == "needs_review"


class TestTirosAPuertaEquipo:
    def test_local_mas_de_5_5(self) -> None:
        assert resultado(pata("team_home_sot_totals", "Over", "5.5"), stats=STATS) == "hit"

    def test_visitante_menos_de_2_5_falla_con_3(self) -> None:
        assert resultado(pata("team_away_sot_totals", "Under", "2.5"), stats=STATS) == "miss"

    def test_partido_suma_los_dos_equipos(self) -> None:
        assert resultado(pata("match_sot_totals", "Over", "8.5"), stats=STATS) == "hit"
        assert resultado(pata("match_sot_totals", "Over", "9.5"), stats=STATS) == "miss"

    def test_linea_entera_igual_al_dato_va_a_revisar(self) -> None:
        assert resultado(pata("match_sot_totals", "Over", "9"), stats=STATS) == "needs_review"


class TestTirosAPuertaJugador:
    STATS_J = StatsPartido(
        local=STATS.local,
        visitante=STATS.visitante,
        jugadores=(
            StatsJugador("Solanke Dominic", shots_on_target=2, minutes_played=90),
            StatsJugador("Marmoush Omar", shots_on_target=0, minutes_played=0),
        ),
    )

    def test_1_o_mas_a_puerta_con_nombre_en_otro_orden(self) -> None:
        leg = pata("player_sot_alt", "Dominic Solanke over 0.5", "0.5", player_name="Dominic Solanke")
        assert resultado(leg, stats=self.STATS_J) == "hit"

    def test_3_o_mas_a_puerta_falla_con_2(self) -> None:
        leg = pata("player_sot_alt", "Dominic Solanke over 2.5", "2.5", player_name="Dominic Solanke")
        assert resultado(leg, stats=self.STATS_J) == "miss"

    def test_jugador_que_no_jugo_va_a_revisar(self) -> None:
        leg = pata("player_sot_alt", "Omar Marmoush over 0.5", "0.5", player_name="Omar Marmoush")
        assert resultado(leg, stats=self.STATS_J) == "needs_review"

    def test_jugador_no_encontrado_va_a_revisar(self) -> None:
        leg = pata("player_sot_alt", "Son over 0.5", "0.5", player_name="Heung-min Son")
        assert resultado(leg, stats=self.STATS_J) == "needs_review"

    def test_sin_stats_de_jugador_espera(self) -> None:
        leg = pata("player_sot_alt", "Dominic Solanke over 0.5", "0.5", player_name="Dominic Solanke")
        assert resultado(leg, stats=STATS) == "pending_stats"

    def test_otros_props_de_jugador_siguen_a_revisar(self) -> None:
        leg = pata("player_card_yes_no", "Frank Onyeka", None, player_name="Frank Onyeka")
        assert resultado(leg, stats=self.STATS_J) == "needs_review"


class TestPendienteDeStats:
    def test_sin_stats_queda_pendiente_de_stats(self) -> None:
        assert resultado(pata("match_sot_totals", "Over", "8.5")) == "pending_stats"

    def test_stats_de_un_solo_equipo_no_bastan(self) -> None:
        solo_local = StatsPartido(local=STATS.local)
        assert resultado(pata("match_sot_totals", "Over", "8.5"), stats=solo_local) == "pending_stats"

    def test_a_las_72_h_pasa_a_revisar(self) -> None:
        leg = pata("match_first_half_totals", "Over", "0.5")
        limite = SAQUE + timedelta(hours=HORAS_ESPERA_STATS)
        assert resultado(leg, now=limite - timedelta(minutes=1)) == "pending_stats"
        assert resultado(leg, now=limite) == "needs_review"

    def test_partido_sin_terminar_sigue_pendiente(self) -> None:
        sin_jugar = partido(None, None, status=MATCH_STATUS_SCHEDULED)
        assert resultado(pata("match_sot_totals", "Over", "8.5"), sin_jugar) == "pending"

    def test_mercados_de_marcador_no_esperan_stats(self) -> None:
        assert resultado(pata("totals", "Over", "2.5")) == "hit"
        assert resultado(pata("h2h", "Tottenham")) == "hit"

    def test_combinada_con_pata_esperando_stats(self) -> None:
        assert overall_from_leg_results(["hit", "pending_stats"]) == "pending_stats"
        assert overall_from_leg_results(["pending", "pending_stats"]) == "pending"
        assert overall_from_leg_results(["miss", "pending_stats"]) == "miss"
        assert overall_from_leg_results(["needs_review", "pending_stats"]) == "pending_stats"


class TestAplazadoEsNula:
    def test_aplazado_es_nula(self) -> None:
        aplazado = partido(None, None, status=MATCH_STATUS_POSTPONED)
        assert resultado(pata("h2h", "Tottenham"), aplazado) == "void"

    def test_cancelado_es_nula(self) -> None:
        cancelado = partido(None, None, status=MATCH_STATUS_CANCELED)
        assert resultado(pata("match_sot_totals", "Over", "8.5"), cancelado) == "void"

    def test_simple_nula(self) -> None:
        assert overall_from_leg_results(["void"]) == "void"

    def test_combinada_nula_y_acertadas_va_a_revisar(self) -> None:
        # La casa recalcula la cuota sin la nula; aquí no se inventa el beneficio.
        assert overall_from_leg_results(["hit", "void", "hit"]) == "needs_review"

    def test_combinada_nula_y_fallo_es_fallo(self) -> None:
        assert overall_from_leg_results(["void", "miss"]) == "miss"

    def test_combinada_nula_y_pendiente_es_pendiente(self) -> None:
        assert overall_from_leg_results(["void", "pending"]) == "pending"

    def test_cuota_efectiva_quita_las_nulas(self) -> None:
        legs = [
            {"result": "hit", "offered_odds": "1.50"},
            {"result": "void", "offered_odds": "2.00"},
            {"result": "hit", "offered_odds": "1.40"},
        ]
        assert str(cuota_efectiva(legs)) == "2.1000"
        assert cuota_efectiva([{"result": "hit", "offered_odds": "1.5"}]) is None
