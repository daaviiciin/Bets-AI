"""Comprueba el consumo de créditos de The Odds API según los snapshots guardados."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import Snapshot


def main() -> int:
    force_utf8_stdio()
    Session = get_session_factory()

    with Session() as s:
        snaps = list(s.scalars(
            select(Snapshot)
            .options(joinedload(Snapshot.league))
            .order_by(Snapshot.captured_at)
        ).all())

    print(f"Total snapshots almacenados: {len(snaps)}\n")
    if not snaps:
        return 0

    print(f"{'#':>2} {'Liga':<35} {'Capturado':<20} {'Mercados':<14} {'Regiones':<10} {'Used':>5} {'Remain':>7}")
    print("-" * 100)
    used_first = None
    used_last = None
    remaining_last = None
    for i, sn in enumerate(snaps, 1):
        liga = sn.league.name if sn.league else "?"
        captured = sn.captured_at.strftime("%Y-%m-%d %H:%M:%S")
        markets = sn.markets or "-"
        regions = sn.regions or "-"
        used = sn.requests_used if sn.requests_used is not None else "?"
        remaining = sn.requests_remaining if sn.requests_remaining is not None else "?"

        if i == 1 and isinstance(used, int):
            used_first = used
        if isinstance(used, int):
            used_last = used
        if isinstance(remaining, int):
            remaining_last = remaining

        print(f"{i:>2} {liga[:35]:<35} {captured:<20} {markets[:14]:<14} {regions:<10} {used!s:>5} {remaining!s:>7}")

    print()
    print("=" * 100)
    print("RESUMEN")
    print("=" * 100)
    if used_first is not None and used_last is not None:
        used_in_session = used_last - used_first + 1  # +1 porque el primer snapshot ya consumió 1
        print(f"Créditos consumidos por TODA esta sesión:   {used_in_session}")
        print(f"Créditos consumidos en TOTAL (cuenta API):  {used_last}")
    if remaining_last is not None:
        print(f"Créditos restantes en la cuenta:            {remaining_last}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
