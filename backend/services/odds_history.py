"""Registro del historial de cuotas (tabla ``odds_history``).

Separa dos partes:

- :func:`plan_history_changes`: lógica pura que, dadas las últimas filas
  conocidas de cada línea y las cuotas observadas ahora, decide qué filas
  nuevas insertar y cuáles extender (``last_seen_at``). Se testea sin BD.
- :class:`OddsHistoryRecorder`: carga el estado de BD, aplica el plan y
  persiste. Se llama desde la ingesta de Winamax, partido a partido.

Una "línea" se identifica por partido + bookie + mercado + jugador +
resultado + punto (p. ej. *Over 2.5*). Solo se guarda una fila nueva cuando
cambia la cuota; si no cambia, se extiende la fila vigente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.models import OddsHistory

# (match_id, bookmaker_id, market_id, player_id, outcome_name, outcome_point)
LineKey = tuple[int, int, int, int | None, str, Decimal | None]


@dataclass(frozen=True)
class LastSeen:
    """Última fila registrada de una línea."""

    history_id: int
    price: Decimal
    last_seen_at: datetime


@dataclass
class HistoryPlan:
    """Qué hacer con el historial tras una observación."""

    to_insert: dict[LineKey, Decimal] = field(default_factory=dict)
    to_extend: list[int] = field(default_factory=list)


def needs_new_row(
    last: LastSeen | None, price: Decimal, *, previous_refresh_at: datetime | None,
) -> bool:
    """Decide si la observación ``price`` abre una fila nueva.

    ``previous_refresh_at`` es el último refresh en el que se vio este partido.
    Si la línea no estaba en ese refresh (su ``last_seen_at`` es anterior),
    desapareció y ha vuelto: se abre fila nueva aunque la cuota sea la misma,
    para que el intervalo ``[first_seen_at, last_seen_at]`` no cubra el hueco.

    ``False`` significa que la fila ``last`` sigue vigente y solo se extiende.
    """
    if last is None:
        return True
    if price != last.price:
        return True
    if previous_refresh_at is not None and last.last_seen_at < previous_refresh_at:
        return True
    return False


def plan_history_changes(
    last_by_key: dict[LineKey, LastSeen],
    observed: dict[LineKey, Decimal],
    *,
    observed_at: datetime,
) -> HistoryPlan:
    """Compara lo observado con lo último registrado y devuelve el plan.

    ``last_by_key`` debe contener todas las líneas del partido (y bookie): de
    ahí se deduce cuándo fue el refresh anterior.
    """
    plan = HistoryPlan()
    previous_refresh_at = max(
        (ls.last_seen_at for ls in last_by_key.values() if ls.last_seen_at < observed_at),
        default=None,
    )
    for key, price in observed.items():
        last = last_by_key.get(key)
        if needs_new_row(last, price, previous_refresh_at=previous_refresh_at):
            plan.to_insert[key] = price
        elif last is not None:
            plan.to_extend.append(last.history_id)
    return plan


class OddsHistoryRecorder:
    """Persiste en ``odds_history`` las cuotas observadas de un partido."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record_match(
        self,
        match_id: int,
        bookmaker_id: int,
        observed: dict[LineKey, Decimal],
        *,
        observed_at: datetime,
    ) -> HistoryPlan:
        """Registra una observación completa de un partido en un bookie.

        No hace ``commit``: va en la misma transacción que la ingesta, así un
        ``rollback`` (o ``--dry-run``) deshace también el historial.
        """
        if not observed:
            return HistoryPlan()
        last_by_key = self._load_last_by_key(match_id, bookmaker_id)
        plan = plan_history_changes(last_by_key, observed, observed_at=observed_at)

        for key, price in plan.to_insert.items():
            m_id, bm_id, mkt_id, player_id, outcome_name, outcome_point = key
            self.session.add(OddsHistory(
                match_id=m_id,
                bookmaker_id=bm_id,
                market_id=mkt_id,
                player_id=player_id,
                outcome_name=outcome_name,
                outcome_point=outcome_point,
                price=price,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            ))
        if plan.to_extend:
            self.session.execute(
                update(OddsHistory)
                .where(OddsHistory.id.in_(plan.to_extend))
                .values(last_seen_at=observed_at)
            )
        return plan

    def _load_last_by_key(self, match_id: int, bookmaker_id: int) -> dict[LineKey, LastSeen]:
        rows = self.session.scalars(
            select(OddsHistory)
            .where(OddsHistory.match_id == match_id)
            .where(OddsHistory.bookmaker_id == bookmaker_id)
            .order_by(OddsHistory.last_seen_at, OddsHistory.id)
        ).all()
        # Orden ascendente: la última asignación de cada clave es la más reciente.
        return {
            line_key(r.match_id, r.bookmaker_id, r.market_id, r.player_id,
                     r.outcome_name, r.outcome_point): LastSeen(r.id, r.price, r.last_seen_at)
            for r in rows
        }


def line_key(
    match_id: int,
    bookmaker_id: int,
    market_id: int,
    player_id: int | None,
    outcome_name: str,
    outcome_point: Decimal | None,
) -> LineKey:
    """Clave normalizada de una línea.

    ``normalize()`` hace que ``2.50`` (leído de BD) y ``2.5`` (del scraper)
    coincidan también en su representación, no solo en ``==``.
    """
    point = outcome_point.normalize() if outcome_point is not None else None
    return (match_id, bookmaker_id, market_id, player_id, outcome_name, point)
