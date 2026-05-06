"""Detecta equipos duplicados (Odds API vs Sofascore) por similaridad de nombre.

Útil tras el primer ingest para identificar pares como
'Real Madrid' (Odds API) vs 'Real Madrid CF' (Sofascore) que pueden necesitar
fusión manual.
"""

from __future__ import annotations

import logging
import re
import sys
from difflib import SequenceMatcher

from sqlalchemy import select

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import Team

SIMILARITY_THRESHOLD = 0.70


def _normalize(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\b(fc|cf|sc|sd|ac|cd|club|the)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    force_utf8_stdio()
    logging.basicConfig(level=logging.WARNING)
    SessionLocal = get_session_factory()

    with SessionLocal() as session:
        teams = session.execute(select(Team).order_by(Team.name)).scalars().all()
        odds_only = [t for t in teams if t.external_source != "sofascore"]
        sofa_only = [t for t in teams if t.external_source == "sofascore"]

        print(f"Total teams: {len(teams)}")
        print(f"  con external_source=sofascore : {len(sofa_only)}")
        print(f"  sin sofascore (solo Odds API) : {len(odds_only)}")
        print()
        print("=== POSIBLES DUPLICADOS (similarity >= 0.70) ===")
        print(f"{'Odds API':30s}  {'Sofascore':30s}  similar")
        print("-" * 80)

        sofa_norm = [(t, _normalize(t.name)) for t in sofa_only]
        printed = 0
        for odds_team in odds_only:
            on = _normalize(odds_team.name)
            if not on:
                continue
            best = max(
                ((t, SequenceMatcher(None, on, sn).ratio()) for t, sn in sofa_norm),
                key=lambda x: x[1],
                default=(None, 0),
            )
            if best[1] >= SIMILARITY_THRESHOLD and best[0] is not None:
                print(f"{odds_team.name[:29]:30s}  {best[0].name[:29]:30s}  {best[1]:.2f}")
                printed += 1

        if not printed:
            print("(sin duplicados sospechosos)")

        print()
        print("=== EQUIPOS HUÉRFANOS (Odds API sin sofascore_id) ===")
        for t in odds_only:
            print(f"  - {t.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
