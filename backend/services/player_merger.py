"""Fusión de jugadores duplicados Sofascore ↔ Flashscore.

Antes de la regla flexible de nombres (:func:`player_names_match`), Flashscore creó
jugadores nuevos para gente que ya existía desde Sofascore con otro nombre ("Paz Nicolás"
frente a "Nico Paz"). Así las stats por partido quedan en una fila y las cuotas de los
*player props* de Winamax (que usan el nombre de Sofascore) en otra.

Un par se fusiona solo si es inequívoco, dentro del mismo equipo:

- el de Sofascore no tiene ``flashscore_id`` y encaja con **un único** jugador de
  Flashscore del equipo;
- el de Flashscore encaja con **un único** jugador del equipo (además de sí mismo).

El de Sofascore se conserva (es el que usan los props y la temporada antigua): recibe el
``flashscore_id``, las stats por partido y las cuotas del duplicado, que se borra. Los
totales de temporada se recalculan después con :func:`rollup_player_season_stats`.

Ejecutar siempre primero sin ``--apply``::

    python -m backend.services.player_merger
    python -m backend.services.player_merger --apply
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import Odds, OddsHistory, Player, PlayerMatchStats, PlayerSeasonStats
from backend.services.entity_resolver import FLASHSCORE_SOURCE
from backend.services.player_season_rollup import rollup_player_season_stats
from backend.services.team_name_matching import player_names_match

SOFASCORE_SOURCE = "sofascore"


@dataclass(frozen=True)
class PlayerRow:
    id: int
    name: str
    team_id: int | None
    source: str | None
    flashscore_id: str | None


@dataclass(frozen=True)
class MergePair:
    keep: PlayerRow       # Sofascore: se conserva
    duplicate: PlayerRow  # Flashscore: se borra


def find_merge_pairs(players: list[PlayerRow]) -> list[MergePair]:
    """Pares inequívocos (mismo equipo, candidato único en las dos direcciones)."""
    by_team: dict[int, list[PlayerRow]] = defaultdict(list)
    for p in players:
        if p.team_id is not None:
            by_team[p.team_id].append(p)

    pairs: list[MergePair] = []
    for team_players in by_team.values():
        flashscore_only = [p for p in team_players if p.source == FLASHSCORE_SOURCE]
        for keep in team_players:
            if keep.source != SOFASCORE_SOURCE or keep.flashscore_id is not None:
                continue
            dups = [d for d in flashscore_only if player_names_match(keep.name, d.name)]
            if len(dups) != 1:
                continue
            dup = dups[0]
            rivals = [
                p for p in team_players
                if p.id != dup.id and player_names_match(dup.name, p.name)
            ]
            if rivals == [keep]:
                pairs.append(MergePair(keep=keep, duplicate=dup))
    return pairs


def load_players(session: Session) -> list[PlayerRow]:
    return [
        PlayerRow(p.id, p.name, p.team_id, p.external_source, p.flashscore_id)
        for p in session.scalars(select(Player)).all()
    ]


def merge_pair(session: Session, pair: MergePair) -> None:
    """Pasa stats y cuotas del duplicado al jugador conservado y borra el duplicado."""
    keep_id, dup_id = pair.keep.id, pair.duplicate.id
    # Stats por partido: si (por lo que sea) ambos tienen el mismo partido, gana el conservado.
    keep_matches = select(PlayerMatchStats.match_id).where(PlayerMatchStats.player_id == keep_id)
    session.execute(delete(PlayerMatchStats).where(
        PlayerMatchStats.player_id == dup_id, PlayerMatchStats.match_id.in_(keep_matches),
    ))
    session.execute(update(PlayerMatchStats).where(PlayerMatchStats.player_id == dup_id)
                    .values(player_id=keep_id))
    # Totales de temporada del duplicado: se recalculan después para el conservado.
    session.execute(delete(PlayerSeasonStats).where(PlayerSeasonStats.player_id == dup_id))
    session.execute(update(Odds).where(Odds.player_id == dup_id).values(player_id=keep_id))
    session.execute(update(OddsHistory).where(OddsHistory.player_id == dup_id)
                    .values(player_id=keep_id))
    # flashscore_id es único: se libera en el duplicado antes de dárselo al conservado.
    dup = session.get(Player, dup_id)
    fs_id = dup.flashscore_id
    dup.flashscore_id = None
    session.flush()
    session.delete(dup)
    session.flush()
    session.get(Player, keep_id).flashscore_id = fs_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fusiona jugadores duplicados Sofascore ↔ Flashscore.")
    parser.add_argument("--apply", action="store_true", help="Aplicar (sin esto, solo lista).")
    args = parser.parse_args(argv)
    force_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("player_merger")

    with get_session_factory()() as session:
        pairs = find_merge_pairs(load_players(session))
        for pair in pairs:
            log.info("  %-32s ← %s", pair.keep.name, pair.duplicate.name)
        log.info("%d pares%s", len(pairs), "" if args.apply else " (simulación: usa --apply)")
        if not args.apply or not pairs:
            return 0
        for pair in pairs:
            merge_pair(session, pair)
        rollup_player_season_stats(session, log=log)
        session.commit()
        log.info("Fusionados %d jugadores y recalculados los totales de temporada.", len(pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
