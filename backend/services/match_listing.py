"""1X2 de Winamax para la lista de partidos (portada del dashboard).

Solo lee el mercado ``h2h`` (3 filas por snapshot y partido), así que es barato aunque la
lista tenga ~100 partidos. De cada partido vale el snapshot más reciente.

Winamax escribe los equipos en español ("Atl. Madrid", "Nápoles", "Hamburgo"): cada
selección se asigna al local o al visitante por :func:`team_side_score`, y se devuelve
también su nombre original, que es el que llevan las cuotas de la página de partido.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Market, Match, Odds, Snapshot
from backend.services.graded_odds_service import WINAMAX_SOURCE
from backend.services.team_name_matching import team_side_score


@dataclass(frozen=True)
class Odds1x2:
    home: Decimal | None
    draw: Decimal | None
    away: Decimal | None
    # Nombre de la selección en Winamax (``outcome_name``) del local y del visitante.
    home_name: str | None = None
    away_name: str | None = None


def outcome_side(name: str, home_team: str, away_team: str) -> str | None:
    """``"home"`` / ``"away"`` para una selección del 1X2; ``None`` si no se puede saber."""
    if name == home_team:
        return "home"
    if name == away_team:
        return "away"
    home, away = team_side_score(home_team, name), team_side_score(away_team, name)
    if home == away:
        return None
    return "home" if home > away else "away"


def pick_1x2(
    rows: list[tuple[str, Decimal, datetime]], home_team: str, away_team: str,
) -> Odds1x2 | None:
    """Filas ``(selección, cuota, captured_at)`` de un partido → 1X2 del snapshot más nuevo.

    La selección es ``Draw`` o el nombre del equipo según Winamax. Las cuotas de más de
    10,00 no se guardan (``MAX_STORED_ODDS``), así que con un favorito muy claro puede
    faltar alguna de las tres. Sin ninguna → ``None``. Esto es solo para mostrar: que una
    cuota aparezca aquí no la hace recomendable (``MAX_RECOMMENDABLE_ODDS``).
    """
    if not rows:
        return None
    newest = max(captured for _, _, captured in rows)
    found: dict[str, tuple[str, Decimal]] = {}
    for name, price, captured in rows:
        if captured != newest:
            continue
        side = "draw" if name == "Draw" else outcome_side(name, home_team, away_team)
        if side is not None:
            found[side] = (name, price)
    if not found:
        return None
    return Odds1x2(
        home=found["home"][1] if "home" in found else None,
        draw=found["draw"][1] if "draw" in found else None,
        away=found["away"][1] if "away" in found else None,
        home_name=found["home"][0] if "home" in found else None,
        away_name=found["away"][0] if "away" in found else None,
    )


def latest_1x2_by_match(session: Session, matches: list[Match]) -> dict[int, Odds1x2]:
    """1X2 más reciente de cada partido con cuotas (los demás no aparecen)."""
    if not matches:
        return {}
    rows = session.execute(
        select(Odds.match_id, Odds.outcome_name, Odds.price, Snapshot.captured_at)
        .join(Snapshot, Snapshot.id == Odds.snapshot_id)
        .join(Market, Market.id == Odds.market_id)
        .where(Market.key == "h2h")
        .where(Snapshot.source == WINAMAX_SOURCE)
        .where(Odds.match_id.in_([m.id for m in matches]))
    ).all()
    by_match: dict[int, list[tuple[str, Decimal, datetime]]] = {}
    for match_id, name, price, captured in rows:
        by_match.setdefault(match_id, []).append((name, price, captured))
    out: dict[int, Odds1x2] = {}
    for m in matches:
        picked = pick_1x2(by_match.get(m.id, []), m.home_team.name, m.away_team.name)
        if picked is not None:
            out[m.id] = picked
    return out


__all__ = ["Odds1x2", "latest_1x2_by_match", "outcome_side", "pick_1x2"]
