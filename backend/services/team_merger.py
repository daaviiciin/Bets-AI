"""Fusión de equipos duplicados Odds API ↔ Sofascore.

Tras el primer ingest de Sofascore quedan filas duplicadas en ``teams``
porque las dos fuentes nombran a los equipos de forma distinta
(``Barcelona`` vs ``FC Barcelona``, ``AC Milan`` vs ``Milan``, etc.).

Este servicio identifica los pares por:

1. **Aliases manuales** (caso especial: ``Athletic Bilbao`` → ``Athletic Club``).
2. **Similitud de cadena** ≥ ``threshold`` (default 0.80).

Estrategia de fusión: el equipo con `external_source IS NULL` (Odds API,
canónico porque tiene los partidos referenciados) recibe el ``external_id``
del duplicado Sofascore y todas sus stats; el duplicado se elimina.

Ejecutar siempre con ``--dry-run`` primero::

    python -m backend.services.team_merger --dry-run
    python -m backend.services.team_merger --apply
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import (
    Match,
    Player,
    PlayerSeasonStats,
    Team,
    TeamSeasonStats,
)

DEFAULT_THRESHOLD = 0.80

# Aliases manuales: nombre Odds API → nombre exacto en Sofascore
MANUAL_ALIASES: dict[str, str] = {
    "Athletic Bilbao": "Athletic Club",
    "Inter Milan": "Inter",
    "Lyon": "Olympique Lyonnais",
    "Marseille": "Olympique de Marseille",
    "Rennes": "Stade Rennais",
    "Brest": "Stade Brestois 29",
    "Alavés": "Deportivo Alavés",
    "Wolverhampton Wanderers": "Wolverhampton",
    "AS Roma": "Roma",
    "Napoli": "SSC Napoli",
    "Oviedo": "Real Oviedo",
}


@dataclass
class MergePair:
    """Par a fusionar."""

    canonical: Team   # se mantiene (tiene partidos del Odds API)
    duplicate: Team   # se elimina (es el creado por Sofascore)
    score: float
    via: str          # "alias" / "similarity"


def _normalize(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\b(fc|cf|sc|sd|ac|cd|club|the)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def find_merge_pairs(session: Session, *, threshold: float = DEFAULT_THRESHOLD) -> list[MergePair]:
    """Empareja equipos duplicados Odds API ↔ Sofascore."""
    teams = session.execute(select(Team)).scalars().all()
    canonicals = [t for t in teams if t.external_source != "sofascore"]
    sofascore = [t for t in teams if t.external_source == "sofascore"]
    sofa_by_name = {t.name: t for t in sofascore}
    sofa_norm = [(t, _normalize(t.name)) for t in sofascore]

    pairs: list[MergePair] = []
    for canonical in canonicals:
        # 1. Alias manual exacto
        alias_target = MANUAL_ALIASES.get(canonical.name)
        if alias_target and alias_target in sofa_by_name:
            pairs.append(MergePair(
                canonical=canonical,
                duplicate=sofa_by_name[alias_target],
                score=1.0,
                via="alias",
            ))
            continue

        # 2. Similitud
        norm = _normalize(canonical.name)
        if not norm:
            continue
        best_team, best_score = None, 0.0
        for sofa_team, sofa_n in sofa_norm:
            score = SequenceMatcher(None, norm, sofa_n).ratio()
            if score > best_score:
                best_score = score
                best_team = sofa_team
        if best_team is not None and best_score >= threshold:
            pairs.append(MergePair(
                canonical=canonical,
                duplicate=best_team,
                score=best_score,
                via="similarity",
            ))

    return pairs


def merge_pair(session: Session, pair: MergePair, *, log: logging.Logger) -> None:
    """Fusiona ``duplicate`` dentro de ``canonical`` (in-place)."""
    canonical, duplicate = pair.canonical, pair.duplicate

    if canonical.id == duplicate.id:
        return

    # 1. external_id del duplicado pasa al canónico (limpiamos el duplicado primero
    #    para evitar violar UNIQUE constraint).
    if duplicate.external_id and not canonical.external_id:
        ext_source, ext_id = duplicate.external_source, duplicate.external_id
        duplicate.external_source = None
        duplicate.external_id = None
        session.flush()
        canonical.external_source = ext_source
        canonical.external_id = ext_id
        session.flush()

    # 2. Reasignar referencias
    session.execute(
        update(TeamSeasonStats)
        .where(TeamSeasonStats.team_id == duplicate.id)
        .values(team_id=canonical.id)
    )
    session.execute(
        update(Player)
        .where(Player.team_id == duplicate.id)
        .values(team_id=canonical.id)
    )
    session.execute(
        update(PlayerSeasonStats)
        .where(PlayerSeasonStats.team_id == duplicate.id)
        .values(team_id=canonical.id)
    )
    session.execute(
        update(Match)
        .where(Match.home_team_id == duplicate.id)
        .values(home_team_id=canonical.id)
    )
    session.execute(
        update(Match)
        .where(Match.away_team_id == duplicate.id)
        .values(away_team_id=canonical.id)
    )

    # 3. Borrar el duplicado
    session.delete(duplicate)
    session.flush()
    log.info("merged: %r ← %r (%s, score=%.2f)",
             canonical.name, duplicate.name, pair.via, pair.score)


def merge_all(
    session: Session,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    apply: bool = False,
    log: logging.Logger | None = None,
) -> tuple[int, list[MergePair]]:
    """Fusiona todos los pares detectados. Devuelve (n_aplicados, pares)."""
    log = log or logging.getLogger(__name__)
    pairs = find_merge_pairs(session, threshold=threshold)

    if not apply:
        return 0, pairs

    applied = 0
    for pair in pairs:
        try:
            merge_pair(session, pair, log=log)
            applied += 1
        except Exception:
            log.exception("Fallo fusionando %r ← %r", pair.canonical.name, pair.duplicate.name)
            session.rollback()
            raise
    session.commit()
    return applied, pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fusiona equipos duplicados Odds API ↔ Sofascore.")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Umbral de similitud (default {DEFAULT_THRESHOLD}).")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="Solo muestra los pares (default).")
    g.add_argument("--apply", action="store_true",
                   help="Ejecuta la fusión.")
    args = parser.parse_args(argv)

    force_utf8_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger(__name__)
    apply = args.apply

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        applied, pairs = merge_all(session, threshold=args.threshold, apply=apply, log=log)

    print()
    print("=" * 80)
    print(f"PARES DETECTADOS ({len(pairs)}) — modo: {'APPLY' if apply else 'DRY-RUN'}")
    print("=" * 80)
    print(f"{'Canonical (Odds API)':30s}  {'Duplicate (Sofascore)':30s}  {'via':12s}  score")
    print("-" * 90)
    for p in pairs:
        print(f"{p.canonical.name[:29]:30s}  {p.duplicate.name[:29]:30s}  {p.via:12s}  {p.score:.2f}")
    print("-" * 90)
    if apply:
        print(f"Fusiones aplicadas: {applied}/{len(pairs)}")
    else:
        print("Re-ejecuta con --apply para aplicar la fusión.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
