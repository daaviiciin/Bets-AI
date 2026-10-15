"""Ingesta de stats post-partido **por jugador** desde Sofascore.

Flujo:

1. Localiza partidos en BD con ``status='finished'`` y ``sofascore_event_id``
   no nulo, que aún no tengan ``PlayerMatchStats``.
2. Para cada uno descarga ``/event/{id}/lineups`` (y opcionalmente
   ``/event/{id}/incidents`` si queremos tarjetas).
3. Hace upsert de ``Player`` por ``external_id`` (Sofascore) y de
   ``PlayerMatchStats`` por ``(match_id, player_id)``.

Idempotente: si ya hay stats para un partido, los actualiza con los valores
nuevos en lugar de duplicar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import Match, Player, PlayerMatchStats, Team
from backend.models.match import MATCH_STATUS_FINISHED
from backend.scrapers import sofascore as ss

EXTERNAL_SOURCE = "sofascore"

# Campos numéricos enteros del modelo. El resto son Decimal (xG, rating, etc).
_INT_FIELDS: frozenset[str] = frozenset({
    "minutes_played", "shirt_number",
    "goals", "assists", "shots", "shots_on_target",
    "shots_off_target", "shots_blocked",
    "big_chances_created", "big_chances_missed",
    "touches", "passes_total", "passes_accurate", "key_passes",
    "crosses_total", "crosses_accurate",
    "long_balls_total", "long_balls_accurate",
    "tackles", "interceptions", "clearances", "blocks",
    "duels_won", "duels_total", "aerial_duels_won",
    "dribbles_succeeded", "dribbles_attempted",
    "fouls_committed", "fouls_drawn", "yellow_cards", "red_cards",
    "offsides", "saves", "goals_conceded",
})

_DECIMAL_FIELDS: frozenset[str] = frozenset({
    "expected_goals", "expected_assists", "goals_prevented", "rating",
})


@dataclass
class PlayerStatsIngestResult:
    """Resumen de una ejecución del ingester."""

    started_at: datetime
    finished_at: datetime
    matches_eligible: int = 0           # finished + sofa_id + sin stats
    matches_processed: int = 0          # los que de verdad se ingirieron
    matches_skipped_already: int = 0    # ya tenían stats
    matches_skipped_no_event_id: int = 0
    matches_failed: int = 0
    players_created: int = 0
    players_updated: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class PlayerMatchStatsIngester:
    """Servicio principal."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        # Inyectables para testear sin red:
        fetch_lineups=ss.fetch_event_lineups,
        fetch_incidents=ss.fetch_event_incidents,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch_lineups = fetch_lineups
        self._fetch_incidents = fetch_incidents

    # -- API pública --------------------------------------------------------

    def ingest(
        self,
        *,
        max_matches: int = 30,
        league_sport_key: str | None = None,
        with_cards: bool = False,
    ) -> PlayerStatsIngestResult:
        """Ejecuta la ingesta para hasta ``max_matches`` partidos.

        :param max_matches: límite de partidos a procesar en una corrida.
        :param league_sport_key: limita a una sola liga (sport_key Odds API).
        :param with_cards: si True, llama también a ``/event/{id}/incidents``
            para añadir yellow/red cards (1 request extra por partido).
        """
        started = datetime.now(timezone.utc)
        result = PlayerStatsIngestResult(started_at=started, finished_at=started)

        eligible = self._find_eligible_matches(
            league_sport_key=league_sport_key, limit=max_matches,
        )
        result.matches_eligible = len(eligible)
        if not eligible:
            result.finished_at = datetime.now(timezone.utc)
            return result

        for match in eligible:
            if not match.sofascore_event_id:
                result.matches_skipped_no_event_id += 1
                continue
            try:
                self._ingest_match(match, with_cards=with_cards, result=result)
                result.matches_processed += 1
            except Exception as exc:  # noqa: BLE001 — captura controlada
                self.log.warning(
                    "Error ingiriendo player stats match=%s sofa=%s: %s",
                    match.id, match.sofascore_event_id, exc,
                )
                result.matches_failed += 1
                result.errors.append(f"match={match.id}: {exc}")
                self.session.rollback()

        self.session.commit()
        result.finished_at = datetime.now(timezone.utc)
        return result

    # -- Pasos internos -----------------------------------------------------

    def _find_eligible_matches(
        self, *, league_sport_key: str | None, limit: int,
    ) -> list[Match]:
        """Partidos finalizados con sofa_id, sin stats de jugador todavía.

        Ordenados por ``played_at`` descendente: empezamos por los más
        recientes (más relevantes para forma reciente del jugador).
        """
        # Subconsulta: match_ids que ya tienen al menos una fila PlayerMatchStats.
        already = select(PlayerMatchStats.match_id).distinct().subquery()

        stmt = (
            select(Match)
            .options(
                joinedload(Match.home_team),
                joinedload(Match.away_team),
                joinedload(Match.league),
            )
            .where(Match.status == MATCH_STATUS_FINISHED)
            .where(Match.sofascore_event_id.is_not(None))
            .where(~Match.id.in_(select(already.c.match_id)))
            .order_by(Match.played_at.desc().nullslast())
            .limit(limit)
        )
        if league_sport_key:
            from backend.models import League
            stmt = stmt.join(Match.league).where(League.sport_key == league_sport_key)

        return list(self.session.scalars(stmt).all())

    def _ingest_match(
        self,
        match: Match,
        *,
        with_cards: bool,
        result: PlayerStatsIngestResult,
    ) -> None:
        sofa_id = match.sofascore_event_id
        assert sofa_id, "_ingest_match espera sofascore_event_id no nulo"

        payload = self._fetch_lineups(sofa_id)
        parsed = ss.parse_event_lineups(payload)

        cards: dict[str, dict[str, int]] = {}
        if with_cards:
            try:
                inc_payload = self._fetch_incidents(sofa_id)
                cards = ss.parse_event_card_counts(inc_payload)
            except Exception as exc:  # noqa: BLE001
                # No es crítico: seguimos sin tarjetas.
                self.log.debug(
                    "Incidents falló para match=%s sofa=%s: %s",
                    match.id, sofa_id, exc,
                )

        for side, team in (("home", match.home_team), ("away", match.away_team)):
            for entry in parsed.get(side, []):
                self._upsert_player_match(
                    match=match,
                    team=team,
                    is_home=(side == "home"),
                    entry=entry,
                    cards=cards,
                    sofa_event_id=sofa_id,
                    result=result,
                )

    def _upsert_player_match(
        self,
        *,
        match: Match,
        team: Team,
        is_home: bool,
        entry: dict,
        cards: dict[str, dict[str, int]],
        sofa_event_id: str,
        result: PlayerStatsIngestResult,
    ) -> None:
        external_id = entry["external_id"]
        player = self._get_or_create_player(
            external_id=external_id,
            name=entry["name"],
            position=entry.get("position"),
            team=team,
            result=result,
        )

        # Mezclar stats + tarjetas + minutos
        stats = dict(entry.get("stats", {}))
        if external_id in cards:
            for k, v in cards[external_id].items():
                stats[k] = v

        existing = self.session.scalar(
            select(PlayerMatchStats).where(
                PlayerMatchStats.match_id == match.id,
                PlayerMatchStats.player_id == player.id,
            )
        )

        if existing is None:
            row = PlayerMatchStats(
                match_id=match.id,
                player_id=player.id,
                team_id=team.id,
                is_home=is_home,
                source=EXTERNAL_SOURCE,
                source_match_id=sofa_event_id,
                captured_at=datetime.now(timezone.utc),
                is_starter=entry.get("is_starter"),
                is_substitute=entry.get("is_substitute"),
                position_in_match=entry.get("position"),
                shirt_number=entry.get("shirt_number"),
            )
            _apply_stats(row, stats)
            self.session.add(row)
            result.rows_inserted += 1
        else:
            existing.captured_at = datetime.now(timezone.utc)
            existing.is_starter = entry.get("is_starter")
            existing.is_substitute = entry.get("is_substitute")
            existing.position_in_match = entry.get("position")
            existing.shirt_number = entry.get("shirt_number")
            existing.team_id = team.id
            existing.is_home = is_home
            _apply_stats(existing, stats)
            result.rows_updated += 1

    def _get_or_create_player(
        self,
        *,
        external_id: str,
        name: str,
        position: str | None,
        team: Team,
        result: PlayerStatsIngestResult,
    ) -> Player:
        player = self.session.scalar(
            select(Player).where(
                Player.external_source == EXTERNAL_SOURCE,
                Player.external_id == external_id,
            )
        )
        if player is not None:
            updated = False
            if name and player.name != name:
                player.name = name
                updated = True
            if position and player.position != position:
                player.position = position
                updated = True
            if player.team_id != team.id:
                player.team_id = team.id
                updated = True
            if updated:
                result.players_updated += 1
            return player

        player = Player(
            name=name or f"sofa_{external_id}",
            external_id=external_id,
            external_source=EXTERNAL_SOURCE,
            position=position,
            team_id=team.id,
        )
        self.session.add(player)
        # Flush para tener el id antes de seguir con PlayerMatchStats.
        self.session.flush()
        result.players_created += 1
        return player


# ---------------------------------------------------------------------------
# Helper público (también útil para tests aislados)
# ---------------------------------------------------------------------------


def _apply_stats(row: PlayerMatchStats, stats: dict) -> None:
    """Asigna las claves de ``stats`` a los campos correspondientes del row.

    * Las claves en ``_INT_FIELDS`` se castean a ``int`` (y se ignoran si no
      casan).
    * Las claves en ``_DECIMAL_FIELDS`` se convierten a ``Decimal``.
    * Cualquier clave no reconocida se ignora silenciosamente.
    """
    for key, value in stats.items():
        if value is None:
            continue
        if key in _INT_FIELDS:
            try:
                setattr(row, key, int(value))
            except (TypeError, ValueError):
                continue
        elif key in _DECIMAL_FIELDS:
            try:
                setattr(row, key, Decimal(str(round(float(value), 4))))
            except (TypeError, ValueError, ArithmeticError):
                continue
        # else: clave desconocida → la ignoramos.


__all__ = [
    "PlayerMatchStatsIngester",
    "PlayerStatsIngestResult",
]
