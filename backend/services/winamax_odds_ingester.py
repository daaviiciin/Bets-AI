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
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.app.config import Settings, get_settings
from backend.models import (
    Bookmaker,
    Market,
    Match,
    Odds,
    OddsHistory,
    Player,
    Snapshot,
)
from backend.models.match import MATCH_STATUS_SCHEDULED
from backend.scrapers import winamax as wm
from backend.services.entity_resolver import find_unique_team_player
from backend.services.team_name_matching import (
    basic_names_match as _names_match,
    basic_normalize_team_name as _normalize,
    team_side_score,
)
from backend.services.odds_history import LineKey, OddsHistoryRecorder, line_key
from backend.services.winamax_normalization import (
    TOTALS_MARKETS,
    canonical_market_key,
    normalize_match_outcome,
    parse_totals_line_from_label,
)

DEFAULT_TIME_WINDOW_HOURS = 4
# Segundo intento de emparejado: misma hora de inicio (Flashscore y Winamax coinciden al minuto).
KICKOFF_FALLBACK_WINDOW = timedelta(minutes=10)
# Pausa entre descargas de detalle de partido (con el calendario completo son ~40 por corrida).
MATCH_REQUEST_SLEEP_SECONDS = 1.0
# Partidos que empiezan antes de esto se refrescan con menos margen (ver needs_odds_refresh).
NEAR_KICKOFF_WINDOW = timedelta(hours=2)
WINAMAX_BOOKMAKER_KEY = "winamax_es"
WINAMAX_BOOKMAKER_TITLE = "Winamax (ES)"
WINAMAX_PLAYER_SOURCE = "winamax-sr"  # Sportradar IDs vienen vía Winamax
# Tope de guardado (= ``DISPLAY_ODDS_CAP`` del frontend): se guarda 1 < cuota <= 10,00.
# Que una cuota se guarde no la hace recomendable: eso lo decide
# ``value_calculator.MAX_RECOMMENDABLE_ODDS`` (4,00).
MAX_STORED_ODDS = Decimal("10.00")

# Claves ``League.sport_key`` alineadas con Flashscore fixtures + Sofascore.
# Orden = reparto de cupos en ingesta (prioriza Premier / Serie A / Bundesliga antes que La Liga).
WINAMAX_TOP5_FOOTBALL_KEYS: tuple[str, ...] = (
    "soccer_epl",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_france_ligue_one",
)

# Torneo de Winamax de cada liga (``League.sport_key`` → ``tournamentId``, de la portada).
# Por el socket, ``tournament:<id>`` trae todos sus partidos con cuota publicada, aunque sean de
# dentro de varios días (el calendario solo cubre 24 h).
WINAMAX_TOURNAMENT_IDS: dict[str, int] = {
    "soccer_epl": 1,
    "soccer_spain_la_liga": 36,
    "soccer_germany_bundesliga": 42,  # ojo: 29 es la de Austria
    "soccer_italy_serie_a": 33,
    "soccer_france_ligue_one": 4,
    "soccer_uefa_champs_league": 151665,
    "soccer_uefa_europa_league": 10909,
    "soccer_uefa_europa_conference_league": 151677,
}
_SPORT_KEY_BY_TOURNAMENT = {tid: key for key, tid in WINAMAX_TOURNAMENT_IDS.items()}

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
    "team_home_sot_totals": "Tiros a puerta equipo local",
    "team_away_sot_totals": "Tiros a puerta equipo visitante",
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
    "player_saves_alt": "Paradas del portero (líneas N o más)",
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
    # Emparejados pero no descargados: sus cuotas se leyeron hace poco (``skip_fresh_minutes``).
    matches_skipped_fresh: int = 0
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
        fetch_calendar=wm.fetch_calendar,
        fetch_tournaments=wm.fetch_tournaments,
        sleep_seconds: float = MATCH_REQUEST_SLEEP_SECONDS,
        fetch_workers: int = 1,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._settings = settings
        self._fetch_overview = fetch_overview
        self._fetch_overview_bb = fetch_overview_basketball
        self._fetch_match = fetch_match
        self._fetch_calendar = fetch_calendar
        self._fetch_tournaments = fetch_tournaments
        self._sleep_seconds = sleep_seconds
        self._fetch_workers = max(1, fetch_workers)
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
        skip_fresh_minutes: int | None = None,
        commit: bool = True,
    ) -> WinamaxIngestResult:
        """Busca partidos próximos en BD y los empareja con Winamax.

        :param sport: ``football`` (home /sport/1) o ``basketball`` (/sport/2).
        :param balance_top5_football: si ``True`` y ``only_top5``, reparte
            ``max_matches`` entre las 5 grandes ligas (evita que la próxima
            cascada horaria sea solo La Liga u otra competición).
        :param skip_fresh_minutes: si se indica, no descarga los partidos cuyas cuotas
            siguen frescas según :func:`needs_odds_refresh` (botón del dashboard).
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
            # La home solo trae ~20 partidos de fútbol; el resto llega por Socket.IO.
            wm_matches = merge_winamax_matches(
                wm.parse_football_matches(overview),
                self._socket_catalog({m.league.sport_key for m in upcoming}),
            )
        self.log.info(
            "Winamax: %d partidos disponibles (sport=%s).",
            len(wm_matches), sport,
        )
        if sport == "football" and len(wm_matches) < 80:
            by_tournament = Counter(
                (m.tournament_name or "(sin nombre)").strip() for m in wm_matches
            )
            self.log.warning(
                "Cobertura Winamax muy baja para fútbol (%s partidos). ¿Falló el calendario "
                "por Socket.IO? Histograma (liga→n): %s",
                len(wm_matches),
                dict(by_tournament.most_common(12)),
            )

        # 3. Bookmaker (los snapshots se crean por liga en el bucle).
        self._bookmaker = self._get_or_create_bookmaker()
        self._scrape_started_at = datetime.now(timezone.utc)
        self._scraped_match_ids_by_league: dict[int, set[int]] = {}

        # 4. Emparejar, descargar el detalle de cada partido y guardarlo.
        pairs: list[tuple[Match, wm.WinamaxMatchInfo]] = []
        for db_match in upcoming:
            wm_info = self._find_winamax_match(db_match, wm_matches)
            if wm_info is None:
                result.matches_unmatched += 1
            else:
                pairs.append((db_match, wm_info))
        if skip_fresh_minutes:
            pairs = self._drop_fresh_pairs(pairs, fresh_minutes=skip_fresh_minutes, result=result)
        states = self._download_match_states([info for _, info in pairs])

        # La sesión de BD no es compartible entre hilos: el guardado va siempre en serie.
        for db_match, wm_info in pairs:
            db_mid = db_match.id
            wm_mid = wm_info.match_id
            try:
                state = states[wm_mid]
                if isinstance(state, Exception):
                    raise state
                snapshot = self._get_or_create_snapshot(db_match.league_id, result)
                self._ingest_one_match(db_match, wm_info, snapshot, result, state)
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
                "Winamax: %d cuotas antiguas > %.2f eliminadas.",
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

    def _socket_catalog(self, sport_keys: Iterable[str | None]) -> list[wm.WinamaxMatchInfo]:
        """Partidos de fútbol de Winamax que llegan por Socket.IO.

        - ``tournament:<id>`` de cada liga nuestra con partidos en la ventana: todos los que
          tienen cuota publicada, aunque sean de dentro de varios días.
        - ``calendar:24``: el resto de competiciones de las próximas 24 h.

        Si una fuente falla se sigue con las demás (como mínimo queda la home).
        """
        catalogs: list[list[wm.WinamaxMatchInfo]] = []
        tournament_ids = winamax_tournament_ids(sport_keys)
        if tournament_ids:
            try:
                states = self._fetch_tournaments(tournament_ids)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("Ligas Winamax (Socket.IO) no disponibles: %s", exc)
                states = {}
            coverage: dict[str, str] = {}
            for tid in tournament_ids:
                key = _SPORT_KEY_BY_TOURNAMENT[tid]
                state = states.get(str(tid))
                if state is None:
                    coverage[key] = "sin respuesta"
                    continue
                matches = wm.parse_football_matches(state)
                catalogs.append(matches)
                # El "partido" del campeón de liga va fechado a final de temporada: fuera del log.
                soon = datetime.now(timezone.utc) + timedelta(days=60)
                last = max((m.kickoff for m in matches if m.kickoff < soon), default=None)
                coverage[key] = f"{len(matches)} hasta {last:%d/%m}" if last else "0"
            self.log.info("Winamax por liga (Socket.IO): %s", coverage)
            if "0" in coverage.values():
                self.log.warning(
                    "Ligas Winamax sin partidos teniendo partidos en BD (¿cambió el id del "
                    "torneo en WINAMAX_TOURNAMENT_IDS?): %s",
                    [k for k, v in coverage.items() if v == "0"],
                )
        try:
            catalogs.append(wm.parse_football_matches(self._fetch_calendar()))
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Calendario Winamax (Socket.IO) no disponible: %s", exc)
        return merge_winamax_matches(*catalogs)

    def _drop_fresh_pairs(
        self,
        pairs: list[tuple[Match, wm.WinamaxMatchInfo]],
        *,
        fresh_minutes: int,
        result: WinamaxIngestResult,
    ) -> list[tuple[Match, wm.WinamaxMatchInfo]]:
        """Quita los partidos cuyas cuotas no hace falta volver a leer todavía.

        No se quedan sin cuotas: :meth:`_carry_forward_odds_from_previous_snapshot` copia
        las del snapshot anterior a los partidos futuros que no se descargan.
        """
        now = datetime.now(timezone.utc)
        last_seen = self._last_seen_by_match([m.id for m, _ in pairs])
        keep = [
            (m, info) for m, info in pairs
            if needs_odds_refresh(
                m.commence_time, last_seen.get(m.id), now, fresh_minutes=fresh_minutes,
            )
        ]
        result.matches_skipped_fresh = len(pairs) - len(keep)
        if result.matches_skipped_fresh:
            self.log.info(
                "Winamax: %d partidos con cuotas aún frescas (margen %d min): se reutilizan.",
                result.matches_skipped_fresh, fresh_minutes,
            )
        return keep

    def _last_seen_by_match(self, match_ids: list[int]) -> dict[int, datetime]:
        """Última vez que se leyeron cuotas de Winamax de cada partido (``odds_history``)."""
        if not match_ids or self._bookmaker is None:
            return {}
        rows = self.session.execute(
            select(OddsHistory.match_id, func.max(OddsHistory.last_seen_at))
            .where(OddsHistory.bookmaker_id == self._bookmaker.id)
            .where(OddsHistory.match_id.in_(match_ids))
            .group_by(OddsHistory.match_id)
        ).all()
        return {match_id: seen for match_id, seen in rows}

    def _download_match_states(
        self, infos: list[wm.WinamaxMatchInfo],
    ) -> dict[str, dict | Exception]:
        """Página de detalle de cada partido (~1,3 s cada una): ``match_id`` → estado o error.

        Con ``fetch_workers == 1`` va de una en una con ``sleep_seconds`` de pausa (tarea
        programada, nadie espera). Con más, descarga en paralelo como haría un navegador
        (botón del dashboard): 36 partidos pasan de ~1 min a ~15 s.
        """
        def fetch(info: wm.WinamaxMatchInfo) -> dict | Exception:
            try:
                return self._fetch_match(info.tournament_id, info.match_id)
            except Exception as exc:  # noqa: BLE001
                return exc

        if self._fetch_workers <= 1:
            out: dict[str, dict | Exception] = {}
            for i, info in enumerate(infos):
                if i and self._sleep_seconds:
                    time.sleep(self._sleep_seconds)
                out[info.match_id] = fetch(info)
            return out
        with ThreadPoolExecutor(max_workers=self._fetch_workers) as pool:
            return dict(zip((i.match_id for i in infos), pool.map(fetch, infos)))

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
        """Elimina las cuotas Winamax por encima del tope de guardado (``> MAX_STORED_ODDS``).

        Es el complemento exacto del límite superior de ``_is_storable_price``: una cuota de
        10,00 se guarda y no se purga.
        """
        snapshot_ids = select(Snapshot.id).where(
            Snapshot.source == WINAMAX_BOOKMAKER_KEY,
        )
        deleted = self.session.execute(
            delete(Odds).where(
                Odds.snapshot_id.in_(snapshot_ids),
                Odds.price > MAX_STORED_ODDS,
            ),
        )
        return int(deleted.rowcount or 0)

    @staticmethod
    def _is_storable_price(price: Decimal) -> bool:
        """Conserva solo cuotas válidas: 1 < cuota <= ``MAX_STORED_ODDS`` (10,00)."""
        return Decimal("1") < price <= MAX_STORED_ODDS

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
            # Winamax escribe en español ("Marsella", "Sparta Praga"): segundo intento por
            # hora de inicio + parecido de nombres, con candidato único.
            return pick_winamax_match_by_kickoff(
                db_match.home_team.name, db_match.away_team.name,
                db_match.commence_time, wm_matches,
            )
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _ingest_one_match(
        self,
        db_match: Match,
        wm_info: wm.WinamaxMatchInfo,
        snapshot: Snapshot,
        result: WinamaxIngestResult,
        state: dict,
    ) -> None:
        """Guarda las cuotas de un partido a partir de su página ya descargada (``state``)."""
        self.log.info(
            "Match db=%s wm=%s: %s vs %s",
            db_match.id, wm_info.match_id, wm_info.home_team, wm_info.away_team,
        )
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


def merge_winamax_matches(
    *lists: list[wm.WinamaxMatchInfo],
) -> list[wm.WinamaxMatchInfo]:
    """Une catálogos de Winamax sin repetir partido; gana la primera aparición.

    La home va primero porque trae ``tournament_name``; el calendario del socket solo
    trae ``tournamentId``.
    """
    seen: dict[str, wm.WinamaxMatchInfo] = {}
    for matches in lists:
        for m in matches:
            seen.setdefault(m.match_id, m)
    return sorted(seen.values(), key=lambda m: (m.kickoff, m.match_id))


def winamax_tournament_ids(sport_keys: Iterable[str | None]) -> list[int]:
    """Torneos de Winamax que hay que pedir para estas ligas (sin repetir, orden estable)."""
    return sorted({WINAMAX_TOURNAMENT_IDS[k] for k in sport_keys if k in WINAMAX_TOURNAMENT_IDS})


def needs_odds_refresh(
    kickoff: datetime,
    last_seen_at: datetime | None,
    now: datetime,
    *,
    fresh_minutes: int,
) -> bool:
    """¿Hay que volver a descargar las cuotas de este partido?

    Lo usa el botón "Actualizar cuotas" para no repetir, en pulsaciones seguidas, partidos
    que se acaban de leer. Si devuelve ``False`` el partido conserva las cuotas anteriores.

    :param kickoff: hora de inicio del partido (UTC).
    :param last_seen_at: última vez que se leyeron sus cuotas de Winamax; ``None`` si nunca.
    :param now: hora actual (UTC); se pasa como parámetro para poder testear.
    :param fresh_minutes: margen elegido para el botón (15 por defecto).
    """
    if last_seen_at is None:
        return True
    margin = timedelta(minutes=fresh_minutes)
    # Cerca del inicio las cuotas se mueven más: margen de un tercio (15 → 5 min).
    if kickoff - now < NEAR_KICKOFF_WINDOW:
        margin /= 3
    return now - last_seen_at >= margin


def pick_winamax_match_by_kickoff(
    home: str,
    away: str,
    kickoff: datetime,
    wm_matches: list[wm.WinamaxMatchInfo],
    *,
    window: timedelta = KICKOFF_FALLBACK_WINDOW,
) -> wm.WinamaxMatchInfo | None:
    """Partido de Winamax que empieza a la misma hora y cuyos equipos se parecen.

    Puntúa cada lado con :func:`team_side_score` (2 = mismo club, 1 = indicio) y exige
    al menos 2 en total y un **único** mejor candidato: en una jornada europea empiezan
    18 partidos a la vez, así que un empate significa que no hay forma segura de elegir.
    """
    scored: list[tuple[int, wm.WinamaxMatchInfo]] = []
    for wmi in wm_matches:
        if abs(wmi.kickoff - kickoff) > window:
            continue
        score = team_side_score(home, wmi.home_team) + team_side_score(away, wmi.away_team)
        if score >= 2:
            scored.append((score, wmi))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


__all__ = [
    "WINAMAX_TOP5_FOOTBALL_KEYS",
    "WINAMAX_TOURNAMENT_IDS",
    "WinamaxOddsIngester",
    "WinamaxIngestResult",
    "needs_odds_refresh",
    "winamax_tournament_ids",
]
