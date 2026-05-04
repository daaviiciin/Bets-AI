"""Procesador de los JSONs ``data/raw/*.json`` hacia PostgreSQL.

Lee los ficheros generados por ``backend.scrapers.odds_api``, los valida con
Pydantic, y los inserta en la BD usando los modelos SQLAlchemy.

Características:
    * Idempotente: si un fichero ya tiene Snapshot asociado en la BD, se omite.
    * Eficiente: cachea Team/Bookmaker/Market/Match/League en memoria para
      evitar queries N+1 sobre los mismos registros.
    * Robusto: cada fichero se procesa en su propia transacción; si falla,
      no contamina el resto.

Uso:

    python -m backend.services.odds_processor             # procesa todos los pendientes
    python -m backend.services.odds_processor --file ...  # procesa un fichero concreto
    python -m backend.services.odds_processor --reprocess # reprocesa aunque ya exista snapshot
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.database import get_session_factory
from backend.models import Bookmaker, League, Market, Match, Odds, Snapshot, Team
from backend.services.schemas import (
    BookmakerEntry,
    EventEntry,
    MarketEntry,
    SnapshotFile,
)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


LEAGUE_METADATA: dict[str, tuple[str, str]] = {
    "soccer_spain_la_liga": ("La Liga", "Spain"),
    "soccer_epl": ("Premier League", "England"),
    "soccer_germany_bundesliga": ("Bundesliga", "Germany"),
    "soccer_italy_serie_a": ("Serie A", "Italy"),
    "soccer_france_ligue_one": ("Ligue 1", "France"),
    "soccer_uefa_champs_league": ("UEFA Champions League", "Europe"),
    "soccer_uefa_europa_league": ("UEFA Europa League", "Europe"),
    "soccer_uefa_europa_conference_league": ("UEFA Conference League", "Europe"),
}

MARKET_METADATA: dict[str, str] = {
    "h2h": "Head to head (1X2)",
    "totals": "Over/Under goals",
    "spreads": "Asian handicap",
    "btts": "Both Teams to Score",
    "double_chance": "Double Chance (1X, X2, 12)",
    "draw_no_bet": "Draw No Bet",
}


@dataclass
class FileResult:
    """Resumen del procesado de un fichero."""

    file_path: Path
    skipped: bool = False
    skip_reason: str | None = None
    snapshot_id: int | None = None
    n_events: int = 0
    n_matches_new: int = 0
    n_odds_inserted: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class _EntityCache:
    """Caché en memoria de entidades ya cargadas/creadas durante el procesado.

    Evita N+1 queries para la misma liga/equipo/bookmaker/mercado/match.
    """

    leagues: dict[str, League] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    bookmakers: dict[str, Bookmaker] = field(default_factory=dict)
    markets: dict[str, Market] = field(default_factory=dict)
    matches: dict[str, Match] = field(default_factory=dict)


class OddsProcessor:
    """Procesa ficheros ``data/raw/*.json`` hacia PostgreSQL."""

    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._cache = _EntityCache()

    def process_file(self, path: Path, *, reprocess: bool = False) -> FileResult:
        """Procesa un único fichero JSON. Idempotente salvo ``reprocess=True``."""
        result = FileResult(file_path=path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = SnapshotFile.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            result.error = f"validation_error: {exc}"
            self.log.error("Fichero inválido %s: %s", path.name, exc)
            return result

        result.n_events = len(data.events)

        existing = self.session.scalar(
            select(Snapshot).where(Snapshot.file_path == str(path))
        )
        if existing and not reprocess:
            result.skipped = True
            result.skip_reason = f"snapshot ya existe (id={existing.id})"
            result.snapshot_id = existing.id
            self.log.info("Saltando %s (ya procesado, snapshot id=%d)", path.name, existing.id)
            return result

        if existing and reprocess:
            self.log.warning("Reprocesando %s (borrando snapshot id=%d)", path.name, existing.id)
            self.session.delete(existing)
            self.session.flush()

        league = self._get_or_create_league(data.metadata.sport_key, data.events)
        snapshot = self._create_snapshot(league, data, str(path))
        self.session.flush()
        result.snapshot_id = snapshot.id

        for event in data.events:
            n_new_match, n_odds = self._process_event(event, league, snapshot)
            result.n_matches_new += n_new_match
            result.n_odds_inserted += n_odds

        self.session.commit()
        self.log.info(
            "Procesado %s -> snapshot=%d eventos=%d matches_nuevos=%d odds=%d",
            path.name, snapshot.id, result.n_events,
            result.n_matches_new, result.n_odds_inserted,
        )
        return result

    def _get_or_create_league(
        self, sport_key: str, events: list[EventEntry]
    ) -> League:
        if sport_key in self._cache.leagues:
            return self._cache.leagues[sport_key]

        league = self.session.scalar(
            select(League).where(League.sport_key == sport_key)
        )
        if league is None:
            name, country = LEAGUE_METADATA.get(
                sport_key,
                (events[0].sport_title if events else sport_key, None),
            )
            league = League(sport_key=sport_key, name=name, country=country)
            self.session.add(league)
            self.session.flush()
            self.log.info("Liga creada: %s (id=%d)", sport_key, league.id)

        self._cache.leagues[sport_key] = league
        return league

    def _get_or_create_team(self, name: str) -> Team:
        if name in self._cache.teams:
            return self._cache.teams[name]

        team = self.session.scalar(select(Team).where(Team.name == name))
        if team is None:
            team = Team(name=name)
            self.session.add(team)
            self.session.flush()

        self._cache.teams[name] = team
        return team

    def _get_or_create_bookmaker(self, entry: BookmakerEntry, regions: str) -> Bookmaker:
        if entry.key in self._cache.bookmakers:
            return self._cache.bookmakers[entry.key]

        bm = self.session.scalar(select(Bookmaker).where(Bookmaker.key == entry.key))
        if bm is None:
            bm = Bookmaker(key=entry.key, title=entry.title, region=regions.split(",")[0])
            self.session.add(bm)
            self.session.flush()
            self.log.info("Bookmaker creado: %s (id=%d)", entry.key, bm.id)

        self._cache.bookmakers[entry.key] = bm
        return bm

    def _get_or_create_market(self, entry: MarketEntry) -> Market:
        if entry.key in self._cache.markets:
            return self._cache.markets[entry.key]

        market = self.session.scalar(select(Market).where(Market.key == entry.key))
        if market is None:
            market = Market(
                key=entry.key,
                name=MARKET_METADATA.get(entry.key, entry.key),
            )
            self.session.add(market)
            self.session.flush()
            self.log.info("Market creado: %s (id=%d)", entry.key, market.id)

        self._cache.markets[entry.key] = market
        return market

    def _get_or_create_match(self, event: EventEntry, league: League) -> tuple[Match, bool]:
        if event.id in self._cache.matches:
            return self._cache.matches[event.id], False

        match = self.session.scalar(select(Match).where(Match.external_id == event.id))
        is_new = False
        if match is None:
            home = self._get_or_create_team(event.home_team)
            away = self._get_or_create_team(event.away_team)
            match = Match(
                external_id=event.id,
                league_id=league.id,
                home_team_id=home.id,
                away_team_id=away.id,
                commence_time=event.commence_time,
            )
            self.session.add(match)
            self.session.flush()
            is_new = True

        self._cache.matches[event.id] = match
        return match, is_new

    def _create_snapshot(
        self, league: League, data: SnapshotFile, file_path: str,
    ) -> Snapshot:
        snapshot = Snapshot(
            league_id=league.id,
            captured_at=data.metadata.captured_at,
            source="the_odds_api",
            file_path=file_path,
            regions=data.metadata.regions,
            markets=data.metadata.markets,
            requests_remaining=data.metadata.requests_remaining,
            requests_used=data.metadata.requests_used,
        )
        self.session.add(snapshot)
        return snapshot

    def _process_event(
        self, event: EventEntry, league: League, snapshot: Snapshot,
    ) -> tuple[int, int]:
        match, is_new = self._get_or_create_match(event, league)

        odds_to_insert: list[Odds] = []
        for bm_entry in event.bookmakers:
            bookmaker = self._get_or_create_bookmaker(bm_entry, snapshot.regions or "eu")
            for market_entry in bm_entry.markets:
                market = self._get_or_create_market(market_entry)
                for outcome in market_entry.outcomes:
                    odds_to_insert.append(Odds(
                        snapshot_id=snapshot.id,
                        match_id=match.id,
                        bookmaker_id=bookmaker.id,
                        market_id=market.id,
                        outcome_name=outcome.name,
                        outcome_point=outcome.point,
                        price=outcome.price,
                        bookmaker_last_update=market_entry.last_update,
                    ))

        if odds_to_insert:
            self.session.add_all(odds_to_insert)

        return (1 if is_new else 0), len(odds_to_insert)


def _configurar_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _listar_ficheros(raw_dir: Path) -> list[Path]:
    return sorted(raw_dir.glob("*.json"))


def _imprimir_resumen(resultados: list[FileResult]) -> None:
    log = logging.getLogger(__name__)
    log.info("=" * 80)
    log.info("RESUMEN DE PROCESADO")
    log.info("=" * 80)
    total_odds = total_matches = 0
    n_ok = n_skip = n_err = 0
    for r in resultados:
        if r.error:
            n_err += 1
            log.info("  [ERR ] %-50s %s", r.file_path.name, r.error)
        elif r.skipped:
            n_skip += 1
            log.info("  [SKIP] %-50s %s", r.file_path.name, r.skip_reason)
        else:
            n_ok += 1
            total_matches += r.n_matches_new
            total_odds += r.n_odds_inserted
            log.info(
                "  [OK  ] %-50s eventos=%d matches_nuevos=%d odds=%d",
                r.file_path.name, r.n_events, r.n_matches_new, r.n_odds_inserted,
            )
    log.info("-" * 80)
    log.info("Ficheros OK / saltados / con error : %d / %d / %d", n_ok, n_skip, n_err)
    log.info("Total matches nuevos               : %d", total_matches)
    log.info("Total odds insertadas              : %d", total_odds)
    log.info("=" * 80)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Procesa ficheros data/raw/*.json hacia PostgreSQL.",
    )
    parser.add_argument(
        "--file", type=Path, default=None,
        help="Procesa un único fichero concreto.",
    )
    parser.add_argument(
        "--reprocess", action="store_true",
        help="Reprocesa ficheros que ya tengan snapshot en BD (los borra y vuelve a insertar).",
    )
    args = parser.parse_args(argv)

    settings: Settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger(__name__)

    if args.file:
        ficheros = [args.file]
    else:
        ficheros = _listar_ficheros(settings.raw_data_dir)

    if not ficheros:
        log.warning("No hay ficheros que procesar en %s", settings.raw_data_dir)
        return 0

    log.info("Procesando %d fichero(s)...", len(ficheros))
    SessionLocal = get_session_factory()
    resultados: list[FileResult] = []

    for fichero in ficheros:
        with SessionLocal() as session:
            processor = OddsProcessor(session, log=log)
            try:
                resultado = processor.process_file(fichero, reprocess=args.reprocess)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                resultado = FileResult(file_path=fichero, error=f"unexpected: {exc}")
                log.exception("Error procesando %s", fichero.name)
            resultados.append(resultado)

    _imprimir_resumen(resultados)
    return 0 if all(r.ok for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
