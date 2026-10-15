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
4. Para player props, enlaza la cuota (``Odds.player_id``) con el jugador de uno de
   los dos equipos del partido cuyo nombre encaje (el mismo que tiene las stats de
   Flashscore). Si no hay ninguno, crea un :class:`Player` por id de Sportradar.

En cada corrida se crean snapshots nuevos por liga; antes del ``commit``
se purgan los antiguos (por defecto se conservan los ``N`` últimos por
liga, véase ``WINAMAX_SNAPSHOT_RETENTION``): las odds ligadas borran por
``ON DELETE CASCADE``.
"""

from __future__ import annotations

import logging
from collections import Counter
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
)
from backend.models.match import MATCH_STATUS_SCHEDULED
from backend.scrapers import winamax as wm
from backend.services.entity_resolver import find_unique_team_player
from backend.services.match_results_updater import _names_match, _normalize
from backend.services.odds_history import LineKey, OddsHistoryRecorder, line_key
from backend.services.winamax_normalization import (
    TOTALS_MARKETS,
    canonical_market_key,
    normalize_match_outcome,
    parse_totals_line_from_label,
)

DEFAULT_TIME_WINDOW_HOURS = 4
WINAMAX_BOOKMAKER_KEY = "winamax_es"
WINAMAX_BOOKMAKER_TITLE = "Winamax (ES)"
WINAMAX_PLAYER_SOURCE = "winamax-sr"  # Sportradar IDs vienen vía Winamax
MAX_STORED_ODDS = Decimal("4")

# Claves ``League.sport_key`` alineadas con Flashscore fixtures + Sofascore.
# Orden = reparto de cupos en ingesta (prioriza Premier / Serie A / Bundesliga antes que La Liga).
WINAMAX_TOP5_FOOTBALL_KEYS: tuple[str, ...] = (
    "soccer_epl",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_france_ligue_one",
)

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
    "match_second_half_totals": "Total de goles (2ª mitad)",
    "team_home_second_half_totals_goals": "Goles local (2ª mitad)",
    "team_away_second_half_totals_goals": "Goles visitante (2ª mitad)",
    "match_second_half_corners_totals": "Total de córneres (2ª mitad)",
    "match_second_half_cards_totals": "Total de tarjetas (2ª mitad)",
    "match_highest_scoring_half": "Mitad con más goles",
    "match_most_goals_half": "Mitad con más goles",
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
    odds_carried_forward: int = 0
    odds_filtered: int = 0
    odds_pruned: int = 0
    history_rows_inserted: int = 0
    history_rows_extended: int = 0
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
        self._history = OddsHistoryRecorder(session)

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
        balance_top5_football: bool = True,
        commit: bool = True,
    ) -> WinamaxIngestResult:
        """Busca partidos próximos en BD y los empareja con Winamax.

        :param sport: ``football`` (home /sport/1) o ``basketball`` (/sport/2).
        :param balance_top5_football: si ``True`` y ``only_top5``, reparte
            ``max_matches`` entre las 5 grandes ligas (evita que la próxima
            cascada horaria sea solo La Liga u otra competición).
        :param commit: si ``False``, deja toda la transacción abierta para
            que el caller pueda hacer ``rollback()`` (útil para ``--dry-run``).
        """
        started = datetime.now(timezone.utc)
        result = WinamaxIngestResult(started_at=started, finished_at=started)

        # 1. Cargar partidos candidatos en BD.
        upcoming = self._load_upcoming_matches(
            days_ahead=days_ahead,
            only_top5=only_top5,
            sport=sport,
            max_matches=max_matches,
            balance_top5_football=balance_top5_football,
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
        if sport == "football" and len(wm_matches) < 80:
            by_tournament = Counter(
                (m.tournament_name or "(sin nombre)").strip() for m in wm_matches
            )
            self.log.warning(
                "Cobertura Winamax muy baja para fútbol (%s partidos en el overview). "
                "La SPA sólo SSR ~50 líneas globales en ``matches``: la Premier inglesa y "
                "otras ligas suelen llegar por Socket.IO, no están en esta descarga HTTP. "
                "Histograma (liga→n): %s",
                len(wm_matches),
                dict(by_tournament.most_common(12)),
            )

        # 3. Bookmaker (los snapshots se crean por liga en el bucle).
        self._bookmaker = self._get_or_create_bookmaker()
        self._scrape_started_at = datetime.now(timezone.utc)
        self._scraped_match_ids_by_league: dict[int, set[int]] = {}

        # 4. Match a match.
        for db_match in upcoming:
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
                self._scraped_match_ids_by_league.setdefault(db_match.league_id, set()).add(db_match.id)
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

        carried_total = 0
        now_carry = datetime.now(timezone.utc)
        result.odds_pruned = self._purge_non_storable_winamax_odds()
        if result.odds_pruned:
            self.log.info(
                "Winamax: %d cuotas antiguas >= %.2f eliminadas.",
                result.odds_pruned,
                MAX_STORED_ODDS,
            )
        for lid, snap in list(self._snapshot_by_league.items()):
            scraped_ids = self._scraped_match_ids_by_league.get(lid, set())
            carried_total += self._carry_forward_odds_from_previous_snapshot(
                lid, snap, scraped_ids, now=now_carry, result=result,
            )
        result.odds_carried_forward = carried_total
        if carried_total:
            self.log.info(
                "Winamax: %d líneas de cuota arrastradas desde snapshot anterior "
                "(partidos futuros no re-scrapeados en esta corrida).",
                carried_total,
            )
        self.session.flush()

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

    def _carry_forward_odds_from_previous_snapshot(
        self,
        league_id: int,
        current_snap: Snapshot,
        scraped_match_ids: set[int],
        *,
        now: datetime,
        result: WinamaxIngestResult | None = None,
    ) -> int:
        """Copia cuotas del snapshot Winamax previo de esta liga para partidos futuros no tocados hoy.

        Sin esto, al crear un snapshot nuevo y purgar los viejos (CASCADE), los partidos que no entran
        en ``max_matches`` pierden todas sus filas ``odds`` hasta la próxima vez que sí se scrapeen.
        """
        prev = self.session.scalar(
            select(Snapshot)
            .where(Snapshot.league_id == league_id)
            .where(Snapshot.source == WINAMAX_BOOKMAKER_KEY)
            .where(Snapshot.id != current_snap.id)
            .order_by(Snapshot.captured_at.desc(), Snapshot.id.desc())
            .limit(1)
        )
        if prev is None:
            return 0
        stmt = (
            select(Odds)
            .where(Odds.snapshot_id == prev.id)
            .join(Match, Match.id == Odds.match_id)
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(Match.commence_time >= now)
        )
        if scraped_match_ids:
            stmt = stmt.where(~Odds.match_id.in_(scraped_match_ids))
        rows = list(self.session.scalars(stmt).all())
        carried = 0
        for o in rows:
            if not self._is_storable_price(o.price):
                if result is not None:
                    result.odds_filtered += 1
                continue
            self.session.add(
                Odds(
                    snapshot_id=current_snap.id,
                    match_id=o.match_id,
                    bookmaker_id=o.bookmaker_id,
                    market_id=o.market_id,
                    player_id=o.player_id,
                    outcome_name=o.outcome_name,
                    outcome_point=o.outcome_point,
                    price=o.price,
                    bookmaker_last_update=o.bookmaker_last_update,
                ),
            )
            carried += 1
        return carried

    def _purge_non_storable_winamax_odds(self) -> int:
        """Elimina del histórico activo las cuotas que ya no cumplen el límite."""
        snapshot_ids = select(Snapshot.id).where(
            Snapshot.source == WINAMAX_BOOKMAKER_KEY,
        )
        deleted = self.session.execute(
            delete(Odds).where(
                Odds.snapshot_id.in_(snapshot_ids),
                Odds.price >= MAX_STORED_ODDS,
            ),
        )
        return int(deleted.rowcount or 0)

    @staticmethod
    def _is_storable_price(price: Decimal) -> bool:
        """Conserva solo cuotas válidas y estrictamente inferiores a 4.00."""
        return price > Decimal("1") and price < MAX_STORED_ODDS

    # ------------------------------------------------------------- Internos

    def _load_upcoming_matches(
        self,
        *,
        days_ahead: int,
        only_top5: bool,
        sport: Literal["football", "basketball"] = "football",
        max_matches: int = 50_000,
        balance_top5_football: bool = True,
    ) -> list[Match]:
        from backend.models import League

        now = datetime.now(timezone.utc)
        until = now + timedelta(days=days_ahead)
        base = (
            select(Match)
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(Match.commence_time >= now)
            .where(Match.commence_time <= until)
            .options(joinedload(Match.home_team), joinedload(Match.away_team))
        )
        if sport == "basketball":
            stmt_bb = base.join(Match.league).where(League.sport_key.ilike("%basketball%"))
            return list(self.session.scalars(stmt_bb.order_by(Match.commence_time)).all())
        if not only_top5:
            return list(self.session.scalars(base.order_by(Match.commence_time)).all())

        keys = WINAMAX_TOP5_FOOTBALL_KEYS
        if not balance_top5_football or max_matches <= 0:
            stmt_t5 = (
                base.join(Match.league)
                .where(League.sport_key.in_(keys))
                .order_by(Match.commence_time)
                .limit(max_matches)
            )
            return list(self.session.scalars(stmt_t5).all())

        per_league = max(1, max_matches // len(keys))
        seen: set[int] = set()
        out: list[Match] = []
        for sk in keys:
            q = (
                base.join(Match.league)
                .where(League.sport_key == sk)
                .order_by(Match.commence_time)
                .limit(per_league)
            )
            for row in self.session.scalars(q).all():
                if row.id not in seen:
                    seen.add(row.id)
                    out.append(row)
        if len(out) < max_matches and seen:
            need = max_matches - len(out)
            q_fill = (
                base.join(Match.league)
                .where(League.sport_key.in_(keys))
                .where(Match.id.not_in(seen))
                .order_by(Match.commence_time)
                .limit(need)
            )
            for row in self.session.scalars(q_fill).all():
                if row.id not in seen:
                    seen.add(row.id)
                    out.append(row)
        elif len(out) < max_matches and not seen:
            # Ningún partido en la 1ª pasada: fallback a orden global.
            stmt_t5 = (
                base.join(Match.league)
                .where(League.sport_key.in_(keys))
                .order_by(Match.commence_time)
                .limit(max_matches)
            )
            return list(self.session.scalars(stmt_t5).all())
        out.sort(key=lambda m: m.commence_time)
        return out[:max_matches]

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
        # Cuotas vistas en esta corrida, para ``odds_history``.
        observed: dict[LineKey, Decimal] = {}

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
                price = Decimal(str(oc.odds))
                if not self._is_storable_price(price):
                    result.odds_filtered += 1
                    continue
                out_name = normalize_match_outcome(
                    canon, oc.label, home_team=home_n, away_team=away_n,
                )
                line: float | None = bet_line
                if canon in TOTALS_MARKETS and line is None:
                    line = (
                        parse_totals_line_from_label(bet.title)
                        or parse_totals_line_from_label(oc.label)
                    )
                outcome_name = _truncate(str(out_name), 128)
                outcome_point = Decimal(str(line)) if line is not None else None
                self.session.add(Odds(
                    snapshot_id=snapshot.id,
                    match_id=db_match.id,
                    bookmaker_id=bookmaker_id,
                    market_id=market.id,
                    player_id=None,
                    outcome_name=outcome_name,
                    outcome_point=outcome_point,
                    price=price,
                    bookmaker_last_update=captured_at,
                ))
                observed[line_key(
                    db_match.id, bookmaker_id, market.id, None, outcome_name, outcome_point,
                )] = price
                result.odds_inserted += 1

        # ---- 2) Player props.
        # El prop no dice de qué equipo es el jugador: se busca entre los jugadores de los
        # dos equipos del partido (nombre flexible, candidato único), para que la cuota
        # quede en el mismo jugador que tiene las stats de Flashscore.
        match_team_ids = [db_match.home_team_id, db_match.away_team_id]
        for prop in props:
            price = Decimal(str(prop.odds))
            if not self._is_storable_price(price):
                result.odds_filtered += 1
                continue
            player = self._get_or_create_player(prop, result, match_team_ids)
            market = self._get_or_create_market(prop.market_key, prop.market_key, result)
            line = prop.line
            outcome_name = _truncate(self._format_outcome_name(prop), 128)
            outcome_point = Decimal(str(line)) if line is not None else None
            player_id = player.id if player is not None else None
            self.session.add(Odds(
                snapshot_id=snapshot.id,
                match_id=db_match.id,
                bookmaker_id=bookmaker_id,
                market_id=market.id,
                player_id=player_id,
                outcome_name=outcome_name,
                outcome_point=outcome_point,
                price=price,
                bookmaker_last_update=captured_at,
            ))
            observed[line_key(
                db_match.id, bookmaker_id, market.id, player_id, outcome_name, outcome_point,
            )] = price
            result.odds_inserted += 1
            result.player_props_inserted += 1

        # Forzar flush para que los IDs queden disponibles si hay
        # iteraciones siguientes que los necesiten.
        self.session.flush()

        plan = self._history.record_match(
            db_match.id, bookmaker_id, observed, observed_at=captured_at,
        )
        result.history_rows_inserted += len(plan.to_insert)
        result.history_rows_extended += len(plan.to_extend)

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
        self,
        prop: wm.WinamaxPlayerProp,
        result: WinamaxIngestResult,
        match_team_ids: list[int],
    ) -> Player | None:
        cache_k = f"{WINAMAX_PLAYER_SOURCE}:{prop.player_external_id or prop.player_name}"
        cached = self._player_external_cache.get(cache_k)
        if cached is not None:
            return cached

        # 1) Jugador de uno de los dos equipos del partido (el que tiene las stats de
        #    Flashscore). No se le renombra: su nombre viene de Flashscore/Sofascore.
        in_match = find_unique_team_player(self.session, prop.player_name, match_team_ids)
        if in_match is not None:
            self._player_external_cache[cache_k] = in_match
            return in_match

        # Sin id de Winamax: último recurso por nombre exacto, sin crear nada.
        if not prop.player_external_id:
            return self.session.scalar(
                select(Player).where(Player.name == prop.player_name).limit(1)
            )

        # 2) Jugador creado antes desde Winamax con el mismo id de Sportradar.
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

        # 3) Nombre exacto único en toda la BD (jugador que aún no tiene equipo asignado).
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


__all__ = ["WINAMAX_TOP5_FOOTBALL_KEYS", "WinamaxOddsIngester", "WinamaxIngestResult"]
