"""Ingesta de cuotas Winamax (player props + match-level) en BD.

Flujo:

1. Para cada partido en BD próximo (status='scheduled', en una ventana
   futura), busca en la home de Winamax un partido equivalente por
   nombres normalizados de equipos + ventana temporal.
2. Cuando encuentra match: descarga la URL completa del partido (
   ``/match/{tournamentId}/{matchId}``) para obtener los 500+ mercados.
3. Persiste los mercados de interés (1X2, totales, player props) en la
   tabla ``Odds``, vinculados al :class:`Snapshot` recién creado y al
   bookmaker ``winamax_es``.
4. Para player props, hace upsert de :class:`Player` por ``external_id``
   (Sportradar) si no existe, y vincula la cuota al jugador via
   ``Odds.player_id``.

En cada corrida se crean snapshots nuevos por liga; antes del ``commit``
se purgan los antiguos (por defecto se conservan los ``N`` últimos por
liga, véase ``WINAMAX_SNAPSHOT_RETENTION``): las odds ligadas borran por
``ON DELETE CASCADE``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.app.config import Settings, get_settings
from backend.models import (
    Bookmaker,
    Market,
    Match,
    Odds,
    Player,
    Snapshot,
    Team,
)
from backend.models.match import MATCH_STATUS_SCHEDULED
from backend.scrapers import winamax as wm
from backend.services.match_results_updater import _names_match, _normalize
from backend.services.winamax_normalization import (
    canonical_market_key,
    normalize_btts_outcome,
    normalize_double_chance_outcome,
    normalize_h2h_outcome,
    normalize_totals_outcome,
    parse_totals_line_from_label,
)

DEFAULT_TIME_WINDOW_HOURS = 4
WINAMAX_BOOKMAKER_KEY = "winamax_es"
WINAMAX_BOOKMAKER_TITLE = "Winamax (ES)"
WINAMAX_PLAYER_SOURCE = "winamax-sr"  # Sportradar IDs vienen vía Winamax

# Friendly names para los mercados que vamos a ingerir.
_MARKET_FRIENDLY_NAME: dict[str, str] = {
    # Match-level
    "match_winner": "Resultado (1X2)",
    "match_winner_dnb": "Ganador sin empate",
    "match_double_chance": "Doble oportunidad",
    "match_totals": "Total de goles",
    "match_btts": "Ambos equipos marcan",
    "match_total_goals_odd_even": "Total de goles - Par/Impar",
    "match_winner_and_totals": "Resultado y total de goles",
    "team_home_totals_goals": "Goles equipo local",
    "team_away_totals_goals": "Goles equipo visitante",
    "match_cards_totals": "Total de tarjetas",
    "match_corners_totals": "Total de córneres",
    "match_shots_totals": "Total de tiros",
    "match_sot_totals": "Total de tiros a puerta",
    "match_first_half_totals": "Total de goles (1ª mitad)",
    "team_home_first_half_totals_goals": "Goles local (1ª mitad)",
    "team_away_first_half_totals_goals": "Goles visitante (1ª mitad)",
    "match_first_half_corners_totals": "Total de córneres (1ª mitad)",
    "match_first_half_cards_totals": "Total de tarjetas (1ª mitad)",
    # Player props
    "player_shots": "Tiros del jugador (over/under)",
    "player_sot": "Tiros a puerta del jugador (over/under)",
    "player_shots_alt": "Tiros del jugador (líneas alt 1+/2+/3+)",
    "player_sot_alt": "Tiros a puerta del jugador (líneas alt)",
    "player_passes_alt": "Pases del jugador (líneas alt)",
    "player_tackles_alt": "Tackles del jugador (líneas alt)",
    "player_assists_alt": "Asistencias del jugador (líneas alt)",
    "player_card_yes_no": "El jugador recibe ≥1 tarjeta",
    "player_anytime_scorer": "Goleador (any time)",
    "player_to_score_2plus": "Marca 2+ goles",
    "player_first_scorer": "Primer goleador",
    "player_last_scorer": "Último goleador",
    "player_scorer_and_team_wins": "Goleador y su equipo gana",
}


@dataclass
class WinamaxIngestResult:
    started_at: datetime
    finished_at: datetime
    matches_in_db: int = 0
    matches_matched: int = 0
    matches_unmatched: int = 0
    matches_failed: int = 0
    snapshots_created: int = 0
    odds_inserted: int = 0
    player_props_inserted: int = 0
    players_created: int = 0
    markets_created: int = 0
    snapshots_pruned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class WinamaxOddsIngester:
    """Servicio principal de ingesta Winamax."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        settings: Settings | None = None,
        # Inyectables para test sin red:
        fetch_overview=wm.fetch_football_overview,
        fetch_overview_basketball=wm.fetch_basketball_overview,
        fetch_match=wm.fetch_match,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._settings = settings
        self._fetch_overview = fetch_overview
        self._fetch_overview_bb = fetch_overview_basketball
        self._fetch_match = fetch_match

        self._bookmaker: Bookmaker | None = None
        self._market_cache: dict[str, Market] = {}
        # Jugadores ya resueltos en esta corrida (evita INSERT duplicado mismo SR id antes del flush).
        self._player_external_cache: dict[str, Player] = {}
        self._snapshot_by_league: dict[int, Snapshot] = {}
        self._scrape_started_at: datetime | None = None

    # ----------------------------------------------------------------- API

    def ingest_upcoming(
        self,
        *,
        days_ahead: int = 7,
        max_matches: int = 10,
        only_top5: bool = True,
        sport: Literal["football", "basketball"] = "football",
        commit: bool = True,
    ) -> WinamaxIngestResult:
        """Busca partidos próximos en BD y los empareja con Winamax.

        :param sport: ``football`` (home /sport/1) o ``basketball`` (/sport/2).
        :param commit: si ``False``, deja toda la transacción abierta para
            que el caller pueda hacer ``rollback()`` (útil para ``--dry-run``).
        """
        started = datetime.now(timezone.utc)
        result = WinamaxIngestResult(started_at=started, finished_at=started)

        # 1. Cargar partidos candidatos en BD.
        upcoming = self._load_upcoming_matches(
            days_ahead=days_ahead, only_top5=only_top5, sport=sport,
        )
        result.matches_in_db = len(upcoming)
        if not upcoming:
            self.log.info("No hay partidos próximos en BD.")
            result.finished_at = datetime.now(timezone.utc)
            return result

        # 2. Descargar overview Winamax.
        if sport == "basketball":
            self.log.info("Descargando home Winamax baloncesto…")
            overview = self._fetch_overview_bb()
            wm_matches = wm.parse_sport_matches(overview, wm.SPORT_BASKETBALL)
        else:
            self.log.info("Descargando home Winamax fútbol…")
            overview = self._fetch_overview()
            wm_matches = wm.parse_football_matches(overview)
        self.log.info(
            "Winamax: %d partidos disponibles (sport=%s).",
            len(wm_matches), sport,
        )

        # 3. Bookmaker (los snapshots se crean por liga en el bucle).
        self._bookmaker = self._get_or_create_bookmaker()
        self._scrape_started_at = datetime.now(timezone.utc)

        # 4. Match a match.
        for db_match in upcoming[:max_matches]:
            wm_info = self._find_winamax_match(db_match, wm_matches)
            if wm_info is None:
                result.matches_unmatched += 1
                continue
            db_mid = db_match.id
            wm_mid = wm_info.match_id
            try:
                snapshot = self._get_or_create_snapshot(db_match.league_id, result)
                self._ingest_one_match(db_match, wm_info, snapshot, result)
                result.matches_matched += 1
            except Exception as exc:  # noqa: BLE001
                self.log.warning(
                    "Error ingiriendo match db=%s wm=%s: %s",
                    db_mid, wm_mid, exc,
                )
                result.matches_failed += 1
                result.errors.append(f"match db={db_mid}: {exc}")
                self.session.rollback()
                self._market_cache.clear()
                self._player_external_cache.clear()
                self._snapshot_by_league.clear()

        touched_leagues = list(self._snapshot_by_league.keys())
        if commit and touched_leagues:
            keep_n = (self._settings or get_settings()).winamax_snapshot_retention
            pruned = self._prune_old_winamax_snapshots(touched_leagues, keep=keep_n)
            result.snapshots_pruned = pruned
            if pruned:
                self.log.info(
                    "Winamax: %d snapshots antiguos borrados (retención %d/liga · %d ligas).",
                    pruned,
                    keep_n,
                    len(set(touched_leagues)),
                )

        if commit:
            self.session.commit()
        result.finished_at = datetime.now(timezone.utc)
        return result

    # ------------------------------------------------------------- Internos

    def _load_upcoming_matches(
        self, *, days_ahead: int, only_top5: bool,
        sport: Literal["football", "basketball"] = "football",
    ) -> list[Match]:
        from backend.models import League
        now = datetime.now(timezone.utc)
        until = now + timedelta(days=days_ahead)
        stmt = (
            select(Match)
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(Match.commence_time >= now)
            .where(Match.commence_time <= until)
            .options(joinedload(Match.home_team), joinedload(Match.away_team))
            .order_by(Match.commence_time)
        )
        if sport == "basketball":
            stmt = stmt.join(Match.league).where(League.sport_key.ilike("%basketball%"))
        elif only_top5:
            top5 = (
                "soccer_spain_la_liga", "soccer_epl",
                "soccer_germany_bundesliga", "soccer_italy_serie_a",
                "soccer_france_ligue_one",
            )
            stmt = stmt.join(Match.league).where(League.sport_key.in_(top5))
        return list(self.session.scalars(stmt).all())

    def _find_winamax_match(
        self, db_match: Match, wm_matches: list[wm.WinamaxMatchInfo],
    ) -> wm.WinamaxMatchInfo | None:
        """Empareja por nombres normalizados de equipos + ventana temporal."""
        home_norm = _normalize(db_match.home_team.name)
        away_norm = _normalize(db_match.away_team.name)
        window = timedelta(hours=DEFAULT_TIME_WINDOW_HOURS)

        candidates: list[tuple[timedelta, wm.WinamaxMatchInfo]] = []
        for wmi in wm_matches:
            wm_home = _normalize(wmi.home_team)
            wm_away = _normalize(wmi.away_team)
            if not (_names_match(home_norm, wm_home) and _names_match(away_norm, wm_away)):
                continue
            delta = abs(wmi.kickoff - db_match.commence_time)
            if delta > window:
                continue
            candidates.append((delta, wmi))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _ingest_one_match(
        self,
        db_match: Match,
        wm_info: wm.WinamaxMatchInfo,
        snapshot: Snapshot,
        result: WinamaxIngestResult,
    ) -> None:
        self.log.info(
            "Match db=%s wm=%s: %s vs %s",
            db_match.id, wm_info.match_id, wm_info.home_team, wm_info.away_team,
        )
        # Detalle completo del partido (con player props).
        state = self._fetch_match(wm_info.tournament_id, wm_info.match_id)
        bets = wm.parse_match_bets(state, wm_info.match_id)
        props = wm.parse_player_props(bets)

        bookmaker_id = self._bookmaker.id  # type: ignore[union-attr]
        captured_at = snapshot.captured_at

        # ---- 1) Cuotas a nivel partido (no player props): h2h, totales,
        #         BTTS, etc. Los persistimos directamente.
        for bet in bets:
            if bet.market_key in wm.PLAYER_PROP_MARKETS:
                continue
            wm_key = bet.market_key
            canon = canonical_market_key(wm_key)
            market = self._get_or_create_market(canon, wm_key, result)
            bet_line = self._line_from_special_value(bet.special_bet_value)
            home_n = db_match.home_team.name
            away_n = db_match.away_team.name
            for oc in bet.outcomes:
                out_name = oc.label
                line: float | None = bet_line
                if canon == "h2h":
                    out_name = normalize_h2h_outcome(oc.label, home_team=home_n, away_team=away_n)
                elif canon in (
                    "totals",
                    "team_home_totals_goals",
                    "team_away_totals_goals",
                    "match_cards_totals",
                    "match_corners_totals",
                    "match_shots_totals",
                    "match_sot_totals",
                    "match_first_half_totals",
                    "team_home_first_half_totals_goals",
                    "team_away_first_half_totals_goals",
                    "match_first_half_corners_totals",
                    "match_first_half_cards_totals",
                ):
                    out_name = normalize_totals_outcome(oc.label)
                    if line is None:
                        line = (
                            parse_totals_line_from_label(bet.title)
                            or parse_totals_line_from_label(oc.label)
                        )
                elif canon == "btts":
                    out_name = normalize_btts_outcome(oc.label)
                elif canon == "double_chance":
                    out_name = normalize_double_chance_outcome(
                        oc.label, home_team=home_n, away_team=away_n,
                    )
                self.session.add(Odds(
                    snapshot_id=snapshot.id,
                    match_id=db_match.id,
                    bookmaker_id=bookmaker_id,
                    market_id=market.id,
                    player_id=None,
                    outcome_name=_truncate(str(out_name), 128),
                    outcome_point=Decimal(str(line)) if line is not None else None,
                    price=Decimal(str(oc.odds)),
                    bookmaker_last_update=captured_at,
                ))
                result.odds_inserted += 1

        # ---- 2) Player props.
        # Mapear nombres de jugadores → home/away team_id (los Sportradar
        # IDs no nos los vamos a guardar como external_id porque el resto
        # de stats viene en Sofascore IDs; usamos nombre + equipo).
        side_team_id = {
            "home": db_match.home_team_id,
            "away": db_match.away_team_id,
        }
        # Sin lineup probable, no podemos saber a qué equipo pertenece
        # cada jugador. Hacemos un upsert "neutro": Player.team_id queda
        # en None si no podemos confirmarlo. (En el futuro se puede
        # enriquecer cruzando con lineups esperadas.)
        for prop in props:
            player = self._get_or_create_player(prop, result)
            market = self._get_or_create_market(prop.market_key, prop.market_key, result)
            line = prop.line
            outcome_name = self._format_outcome_name(prop)
            self.session.add(Odds(
                snapshot_id=snapshot.id,
                match_id=db_match.id,
                bookmaker_id=bookmaker_id,
                market_id=market.id,
                player_id=player.id if player is not None else None,
                outcome_name=_truncate(outcome_name, 128),
                outcome_point=Decimal(str(line)) if line is not None else None,
                price=Decimal(str(prop.odds)),
                bookmaker_last_update=captured_at,
            ))
            result.odds_inserted += 1
            result.player_props_inserted += 1

        # Forzar flush para que los IDs queden disponibles si hay
        # iteraciones siguientes que los necesiten.
        self.session.flush()

    # -------- Helpers de "get or create"

    def _get_or_create_snapshot(
        self, league_id: int, result: WinamaxIngestResult,
    ) -> Snapshot:
        cached = self._snapshot_by_league.get(league_id)
        if cached is not None:
            return cached
        snap = Snapshot(
            league_id=league_id,
            captured_at=self._scrape_started_at or datetime.now(timezone.utc),
            source=WINAMAX_BOOKMAKER_KEY,
            markets="winamax_full",
        )
        self.session.add(snap)
        self.session.flush()
        self._snapshot_by_league[league_id] = snap
        result.snapshots_created += 1
        return snap

    def _get_or_create_bookmaker(self) -> Bookmaker:
        bm = self.session.scalar(
            select(Bookmaker).where(Bookmaker.key == WINAMAX_BOOKMAKER_KEY)
        )
        if bm is not None:
            return bm
        bm = Bookmaker(
            key=WINAMAX_BOOKMAKER_KEY,
            title=WINAMAX_BOOKMAKER_TITLE,
            region="es",
        )
        self.session.add(bm)
        self.session.flush()
        return bm

    def _prune_old_winamax_snapshots(self, league_ids: list[int], *, keep: int) -> int:
        """Elimina snapshots ``winamax_es`` más antiguos que los últimos ``keep`` por liga."""
        uniq = sorted({lid for lid in league_ids if lid is not None})
        if not uniq or keep < 1:
            return 0

        rn = func.row_number().over(
            partition_by=Snapshot.league_id,
            order_by=(Snapshot.captured_at.desc(), Snapshot.id.desc()),
        ).label("rn")
        ranked = (
            select(Snapshot.id, rn)
            .where(Snapshot.source == WINAMAX_BOOKMAKER_KEY)
            .where(Snapshot.league_id.in_(uniq))
        ).subquery()
        stale_ids = select(ranked.c.id).where(ranked.c.rn > keep)
        res = self.session.execute(delete(Snapshot).where(Snapshot.id.in_(stale_ids)))
        return int(res.rowcount or 0)

    def _get_or_create_market(
        self,
        canonical_key: str,
        wm_source_key: str,
        result: WinamaxIngestResult,
    ) -> Market:
        cached = self._market_cache.get(canonical_key)
        if cached is not None:
            return cached
        existing = self.session.scalar(select(Market).where(Market.key == canonical_key))
        if existing is not None:
            self._market_cache[canonical_key] = existing
            return existing

        name = _MARKET_FRIENDLY_NAME.get(wm_source_key, canonical_key)
        try:
            with self.session.begin_nested():
                market = Market(key=canonical_key, name=name)
                self.session.add(market)
                self.session.flush()
            result.markets_created += 1
            self._market_cache[canonical_key] = market
            return market
        except IntegrityError:
            # Otro proceso (u otra parte de la misma corrida) insertó el mercado.
            fresh = self.session.scalar(select(Market).where(Market.key == canonical_key))
            if fresh is None:
                raise
            self._market_cache[canonical_key] = fresh
            return fresh

    def _get_or_create_player(
        self, prop: wm.WinamaxPlayerProp, result: WinamaxIngestResult,
    ) -> Player | None:
        # Si Winamax no nos dio external_id, buscamos por nombre simple
        # (último recurso). De momento nos limitamos a los que sí lo traen.
        if not prop.player_external_id:
            # Intentar match por nombre
            existing = self.session.scalar(
                select(Player).where(Player.name == prop.player_name).limit(1)
            )
            return existing

        cache_k = f"{WINAMAX_PLAYER_SOURCE}:{prop.player_external_id}"
        cached = self._player_external_cache.get(cache_k)
        if cached is not None:
            if prop.player_name and cached.name != prop.player_name:
                cached.name = prop.player_name
            return cached

        player = self.session.scalar(
            select(Player).where(
                Player.external_source == WINAMAX_PLAYER_SOURCE,
                Player.external_id == prop.player_external_id,
            )
        )
        if player is not None:
            if prop.player_name and player.name != prop.player_name:
                player.name = prop.player_name
            self._player_external_cache[cache_k] = player
            return player

        # Probar matching cruzado con un jugador Sofascore por nombre
        # (mejor que crear duplicado). Si el nombre encaja exactamente y
        # hay sólo un Player con ese nombre, lo reutilizamos pero NO le
        # tocamos su external_id (Sofascore ya lo tiene).
        existing_by_name = list(self.session.scalars(
            select(Player).where(Player.name == prop.player_name)
        ).all())
        if len(existing_by_name) == 1:
            p = existing_by_name[0]
            self._player_external_cache[cache_k] = p
            return p

        # No encontrado: creamos Player winamax-sr (una sola vez por corrida /
        # SR id gracias a _player_external_cache y begin_nested ante carreras).
        player = Player(
            name=prop.player_name or f"sr_{prop.player_external_id}",
            external_id=prop.player_external_id,
            external_source=WINAMAX_PLAYER_SOURCE,
        )
        try:
            with self.session.begin_nested():
                self.session.add(player)
                self.session.flush()
            result.players_created += 1
            self._player_external_cache[cache_k] = player
            return player
        except IntegrityError:
            fresh = self.session.scalar(
                select(Player).where(
                    Player.external_source == WINAMAX_PLAYER_SOURCE,
                    Player.external_id == prop.player_external_id,
                )
            )
            if fresh is None:
                raise
            if prop.player_name and fresh.name != prop.player_name:
                fresh.name = prop.player_name
            self._player_external_cache[cache_k] = fresh
            return fresh

    # -------- Helpers de formato

    @staticmethod
    def _line_from_special_value(sbv: str | None) -> float | None:
        if not sbv or "total=" not in sbv:
            return None
        try:
            chunk = sbv.split("total=", 1)[1]
            chunk = chunk.split("|", 1)[0]
            return float(chunk.replace(",", "."))
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _format_outcome_name(prop: wm.WinamaxPlayerProp) -> str:
        if prop.line is None:
            return prop.player_name or prop.raw_label
        if prop.side == "over":
            return f"{prop.player_name} over {prop.line}"
        if prop.side == "under":
            return f"{prop.player_name} under {prop.line}"
        return f"{prop.player_name} {prop.side} {prop.line}"


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


__all__ = ["WinamaxOddsIngester", "WinamaxIngestResult"]
