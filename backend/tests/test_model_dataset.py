"""Tests del resumen de cobertura del diagnóstico B0 (``model_dataset``), sin BD."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.services.model_dataset import (
    FilaPartido,
    equipos_liga_y_uefa,
    partidos_con_posible_prorroga,
    puede_ir_a_prorroga,
    resumen_cobertura,
    resumen_mercados,
)

LIGA = "soccer_spain_la_liga"
UCL = "soccer_uefa_champs_league"
OCT = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)


def fila(mid: int, league: str = "La Liga", sport_key: str = LIGA, *, home=1, away=2,
         stats=2, xg=2, mitades=2, sot=2, cuando: datetime = OCT) -> FilaPartido:
    return FilaPartido(
        match_id=mid, sport_key=sport_key, league=league, commence_time=cuando,
        home_team_id=home, away_team_id=away,
        lados_con_stats=stats, lados_con_xg=xg, lados_con_mitades=mitades, lados_con_sot=sot,
    )


class TestResumenCobertura:
    def test_cuenta_solo_partidos_con_los_dos_lados(self) -> None:
        filas = [
            fila(1),
            fila(2, xg=1, mitades=0),
            fila(3, stats=0, xg=0, mitades=0, sot=0),
            fila(4, "UEFA Champions League", UCL),
        ]
        res = {c.league: c for c in resumen_cobertura(filas)}
        liga = res["La Liga"]
        assert (liga.partidos, liga.con_stats, liga.con_xg, liga.con_mitades, liga.con_sot) == (
            3, 2, 1, 1, 2,
        )
        assert liga.pct_stats == Decimal("66.7")
        assert liga.pct_xg == Decimal("33.3")

    def test_total_al_final(self) -> None:
        res = resumen_cobertura([fila(1), fila(2, "Serie A", stats=0, xg=0, mitades=0, sot=0)])
        assert [c.league for c in res] == ["La Liga", "Serie A", "TOTAL"]
        assert res[-1].partidos == 2
        assert res[-1].con_stats == 1
        assert res[-1].pct_stats == Decimal("50.0")

    def test_sin_partidos_no_hay_filas(self) -> None:
        assert resumen_cobertura([]) == []


class TestEquiposLigaYUefa:
    def test_solo_los_que_juegan_las_dos(self) -> None:
        filas = [
            fila(1, home=1, away=2),
            fila(2, "UEFA Champions League", UCL, home=1, away=9),
        ]
        assert equipos_liga_y_uefa(filas) == {1}


class TestRiesgoDeProrroga:
    def test_ligas_domesticas_nunca(self) -> None:
        assert not puede_ir_a_prorroga(LIGA, datetime(2027, 3, 1, tzinfo=timezone.utc))

    def test_fase_de_liga_uefa_no(self) -> None:
        assert not puede_ir_a_prorroga(UCL, OCT)
        assert not puede_ir_a_prorroga(UCL, datetime(2027, 1, 28, tzinfo=timezone.utc))

    def test_eliminatorias_y_previas_uefa_si(self) -> None:
        assert puede_ir_a_prorroga(UCL, datetime(2027, 2, 17, tzinfo=timezone.utc))
        assert puede_ir_a_prorroga(UCL, datetime(2026, 8, 26, tzinfo=timezone.utc))

    def test_lista_de_partidos_con_riesgo(self) -> None:
        feb = datetime(2027, 2, 17, tzinfo=timezone.utc)
        filas = [fila(1), fila(2, "UEFA Champions League", UCL, cuando=feb)]
        assert [f.match_id for f in partidos_con_posible_prorroga(filas)] == [2]


class TestResumenMercados:
    def test_suma_y_ordena_de_mayor_a_menor(self) -> None:
        res = resumen_mercados([("totals", 5), ("btts", 7), ("totals", 3), ("h2h", 7)])
        assert res == [("totals", 8), ("btts", 7), ("h2h", 7)]
