"""Diagnóstico de datos para el modelo v2 (fase 3, bloque B0). Solo lectura.

Uso::

    python -m backend.tools.diagnostico_modelo

Mide qué datos hay para entrenar y liquidar: partidos terminados y cobertura de
``match_stats`` por liga, equipos que juegan liga y UEFA, mercados presentes en ``odds`` y
``odds_history``, stats de jugador por partido, estados de partido, valor efectivo de
``official_data_pipeline`` y el riesgo de prórroga. No escribe nada en la BD.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.models import League, Market, Match, MatchStats, Odds, OddsHistory, PlayerMatchStats
from backend.models.match import MATCH_STATUS_FINISHED
from backend.services.model_dataset import (
    FilaPartido,
    equipos_liga_y_uefa,
    partidos_con_posible_prorroga,
    resumen_cobertura,
    resumen_mercados,
)

#: Mercados que interesan para la fase 3 (se listan aunque tengan 0 filas).
MERCADOS_CLAVE: tuple[str, ...] = (
    "double_chance",
    "match_sot_totals",
    "team_home_sot_totals",
    "team_away_sot_totals",
    "match_first_half_totals",
    "team_home_first_half_totals_goals",
    "team_away_first_half_totals_goals",
    "match_second_half_totals",
    "team_home_second_half_totals_goals",
    "team_away_second_half_totals_goals",
    "player_sot",
    "player_sot_alt",
)


def _pct(v: object) -> str:
    return "  —  " if v is None else f"{v:>5}%"


def cargar_filas(session: Session) -> list[FilaPartido]:
    """Una :class:`FilaPartido` por partido terminado, con los lados que tienen cada stat."""
    ms = (
        select(
            MatchStats.match_id.label("mid"),
            func.count().label("n"),
            func.count(MatchStats.xg).label("n_xg"),
            func.sum(
                case(
                    (MatchStats.goals_1h.is_not(None) & MatchStats.goals_2h.is_not(None), 1),
                    else_=0,
                ),
            ).label("n_mitades"),
            func.count(MatchStats.shots_on_target).label("n_sot"),
        )
        .group_by(MatchStats.match_id)
        .subquery()
    )
    stmt = (
        select(
            Match.id, League.sport_key, League.name, Match.commence_time,
            Match.home_team_id, Match.away_team_id,
            ms.c.n, ms.c.n_xg, ms.c.n_mitades, ms.c.n_sot,
        )
        .join(League, League.id == Match.league_id)
        .outerjoin(ms, ms.c.mid == Match.id)
        .where(Match.status == MATCH_STATUS_FINISHED)
    )
    return [
        FilaPartido(
            match_id=r[0], sport_key=r[1], league=r[2], commence_time=r[3],
            home_team_id=r[4], away_team_id=r[5],
            lados_con_stats=int(r[6] or 0), lados_con_xg=int(r[7] or 0),
            lados_con_mitades=int(r[8] or 0), lados_con_sot=int(r[9] or 0),
        )
        for r in session.execute(stmt).all()
    ]


def _imprimir_cobertura(filas: list[FilaPartido]) -> None:
    print("\n=== PARTIDOS TERMINADOS Y COBERTURA DE match_stats (los dos lados) ===")
    print(f"{'Liga':28s} {'Part.':>5} {'Stats':>7} {'xG':>7} {'Mitades':>7} {'SoT':>7}")
    for c in resumen_cobertura(filas):
        print(
            f"{c.league[:28]:28s} {c.partidos:>5} {_pct(c.pct_stats):>7} {_pct(c.pct_xg):>7} "
            f"{_pct(c.pct_mitades):>7} {_pct(c.pct_sot):>7}"
        )
    if filas:
        desde = min(f.commence_time for f in filas)
        hasta = max(f.commence_time for f in filas)
        print(f"  Rango: {desde:%Y-%m-%d} → {hasta:%Y-%m-%d}")


def _imprimir_mercados(session: Session, modelo: type, titulo: str) -> None:
    stmt = (
        select(Market.key, func.count(), func.count(func.distinct(modelo.match_id)))
        .join(modelo, modelo.market_id == Market.id)
        .group_by(Market.key)
    )
    filas = session.execute(stmt).all()
    partidos = {k: p for k, _, p in filas}
    print(f"\n=== MERCADOS EN {titulo} (filas · partidos) ===")
    for key, n in resumen_mercados((k, n) for k, n, _ in filas):
        marca = " *" if key in MERCADOS_CLAVE else ""
        print(f"  {key:40s} {n:>8} · {partidos.get(key, 0):>4}{marca}")
    faltan = [k for k in MERCADOS_CLAVE if k not in partidos]
    if faltan:
        print(f"  Sin filas (*): {', '.join(faltan)}")


def _imprimir_jugadores(session: Session) -> None:
    print("\n=== STATS DE JUGADOR POR PARTIDO (player_match_stats) ===")
    total, con_sot, partidos = session.execute(
        select(
            func.count(),
            func.count(PlayerMatchStats.shots_on_target),
            func.count(func.distinct(PlayerMatchStats.match_id)),
        ),
    ).one()
    por_fuente = session.execute(
        select(PlayerMatchStats.source, func.count()).group_by(PlayerMatchStats.source),
    ).all()
    consultados = session.scalar(
        select(func.count()).select_from(Match).where(
            Match.status == MATCH_STATUS_FINISHED, Match.player_stats_fetched_at.is_not(None),
        ),
    )
    print(f"  Filas: {total} · con tiros a puerta: {con_sot} · partidos: {partidos}")
    print(f"  Por fuente: {', '.join(f'{s}={n}' for s, n in por_fuente) or '—'}")
    print(f"  Partidos terminados con stats de jugador consultadas: {consultados}")


def _imprimir_estados(session: Session) -> None:
    print("\n=== ESTADOS DE PARTIDO ===")
    for st, n in session.execute(select(Match.status, func.count()).group_by(Match.status)).all():
        print(f"  {st:12s} {n}")


def main() -> int:
    force_utf8_stdio()
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    SessionLocal = get_session_factory()

    with SessionLocal() as session:
        filas = cargar_filas(session)
        _imprimir_cobertura(filas)

        dobles = equipos_liga_y_uefa(filas)
        print(f"\n=== EQUIPOS EN LIGA Y UEFA === {len(dobles)}")

        _imprimir_mercados(session, OddsHistory, "odds_history")
        _imprimir_mercados(session, Odds, "odds")
        _imprimir_jugadores(session)
        _imprimir_estados(session)

        print("\n=== CONFIGURACIÓN ===")
        print(f"  official_data_pipeline (get_settings): {settings.official_data_pipeline}")

        print("\n=== PRÓRROGA ===")
        print("  La BD no guarda si un partido fue a prórroga/penaltis (Match solo tiene status,")
        print("  home_score y away_score; el parser de Flashscore solo mira AB=3 'terminado').")
        posibles = partidos_con_posible_prorroga(filas)
        print(f"  Partidos terminados que PODRÍAN haber ido a prórroga (UEFA fuera de la fase de liga): "
              f"{len(posibles)}")
        for f in posibles[:20]:
            print(f"    {f.commence_time:%Y-%m-%d} {f.league} (match_id={f.match_id})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
