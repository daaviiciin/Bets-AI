"""Servicio de refresco completo de cuotas.

Lógica:

1. Capturamos un timestamp ``started_at`` antes de empezar.
2. Lanzamos el scraper para las 8 competiciones (esto consume créditos de la API).
3. Procesamos los JSONs recién generados hacia la BD.
4. **Solo si los pasos previos fueron OK**, limpiamos la BD:

   * Borramos snapshots con ``captured_at < started_at`` (cascade borra sus odds).
   * Borramos partidos cuyo ``commence_time`` ya pasó (con un colchón de
     ``stale_match_buffer`` horas).

Este orden garantiza que si el scraping falla a la mitad, conservamos los datos
antiguos en lugar de quedarnos sin nada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.models import Match, Odds, Snapshot
from backend.scrapers import odds_api
from backend.services.odds_processor import OddsProcessor

DEFAULT_STALE_MATCH_BUFFER_HOURS = 6


@dataclass
class RefreshSummary:
    """Resumen del refresco para devolver vía API."""

    started_at: datetime
    finished_at: datetime
    leagues_total: int
    leagues_ok: int
    leagues_error: int
    snapshots_deleted: int = 0
    past_matches_deleted: int = 0
    matches_inserted: int = 0
    odds_inserted: int = 0
    requests_used_total: int | None = None
    requests_remaining: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def ok(self) -> bool:
        return self.leagues_error == 0


class RefreshService:
    """Orquestador del refresco completo (scraping + ingest + cleanup)."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.log = log or logging.getLogger(__name__)

    def refresh_all(
        self,
        *,
        stale_match_buffer_hours: int = DEFAULT_STALE_MATCH_BUFFER_HOURS,
    ) -> RefreshSummary:
        """Refresca cuotas de todas las ligas y limpia datos obsoletos."""
        started_at = datetime.now(timezone.utc)
        summary = RefreshSummary(
            started_at=started_at,
            finished_at=started_at,
            leagues_total=len(odds_api.LIGAS),
            leagues_ok=0,
            leagues_error=0,
        )

        self.log.info("=" * 70)
        self.log.info("Iniciando refresh completo a las %s", started_at.isoformat())
        self.log.info("=" * 70)

        if not self.settings.odds_api_refresh_enabled or not (
            self.settings.odds_api_key or ""
        ).strip():
            summary.finished_at = datetime.now(timezone.utc)
            summary.leagues_error = summary.leagues_total
            msg = (
                "Refresco Odds API desactivado "
                "(odds_api_refresh_enabled=false o ODDS_API_KEY vacía); no se ejecuta."
            )
            summary.errors.insert(0, msg)
            self.log.warning(msg)
            return summary

        results = odds_api.fetch_todas(settings=self.settings)
        successful_files: list[Path] = []
        for r in results:
            if r.ok and r.file_path:
                summary.leagues_ok += 1
                successful_files.append(r.file_path)
            else:
                summary.leagues_error += 1
                if r.error:
                    summary.errors.append(f"{r.liga}: {r.error}")
            if r.requests_used is not None:
                summary.requests_used_total = r.requests_used
            if r.requests_remaining is not None:
                summary.requests_remaining = r.requests_remaining

        if not successful_files:
            summary.finished_at = datetime.now(timezone.utc)
            summary.errors.insert(0, "Ningún fichero capturado, abortando refresh.")
            self.log.error("No hay ficheros para procesar; conservamos los datos antiguos.")
            return summary

        processor = OddsProcessor(self.session, log=self.log)
        for path in successful_files:
            try:
                file_result = processor.process_file(path, reprocess=True)
                summary.matches_inserted += file_result.n_matches_new
                summary.odds_inserted += file_result.n_odds_inserted
            except Exception as exc:
                self.session.rollback()
                self.log.exception("Error procesando %s", path.name)
                summary.errors.append(f"process {path.name}: {exc}")

        snapshots_deleted = self._delete_old_snapshots(started_at)
        summary.snapshots_deleted = snapshots_deleted

        cutoff_match = datetime.now(timezone.utc) - timedelta(hours=stale_match_buffer_hours)
        past_deleted = self._delete_past_matches(cutoff_match)
        summary.past_matches_deleted = past_deleted

        summary.finished_at = datetime.now(timezone.utc)
        self.log.info(
            "Refresh completado en %.1fs: ligas %d/%d, snapshots viejos borrados=%d, "
            "partidos pasados borrados=%d, matches nuevos=%d, odds nuevas=%d",
            summary.duration_seconds, summary.leagues_ok, summary.leagues_total,
            summary.snapshots_deleted, summary.past_matches_deleted,
            summary.matches_inserted, summary.odds_inserted,
        )
        return summary

    def _delete_old_snapshots(self, cutoff: datetime) -> int:
        """Borra los snapshots anteriores al refresh (cascade borra sus odds)."""
        n_before = self.session.scalar(
            select(func.count()).select_from(Snapshot)
            .where(Snapshot.captured_at < cutoff)
        ) or 0
        if n_before == 0:
            return 0

        odds_orphan = self.session.scalar(
            select(func.count()).select_from(Odds)
            .join(Odds.snapshot)
            .where(Snapshot.captured_at < cutoff)
        ) or 0

        self.session.execute(
            delete(Snapshot).where(Snapshot.captured_at < cutoff),
        )
        self.session.commit()
        self.log.info(
            "Borrados %d snapshots antiguos (y %d odds asociadas en cascada)",
            n_before, odds_orphan,
        )
        return n_before

    def _delete_past_matches(self, cutoff: datetime) -> int:
        """Borra los partidos pasados que NO tengan resultado registrado.

        Si un partido ya tiene ``status='finished'`` (lo actualizó el
        ``MatchResultsUpdater``), lo conservamos para tracking histórico,
        backtest y para que el frontend pueda mostrar el resultado real.
        Sí borramos sus cuotas en cascada — esas ya no aportan valor —
        pero el ``Match`` permanece. Si más adelante interesa purgar
        muy antiguos, se hace en un job aparte.
        """
        from backend.models.match import MATCH_STATUS_FINISHED

        n_before = self.session.scalar(
            select(func.count()).select_from(Match)
            .where(Match.commence_time < cutoff)
            .where(Match.status != MATCH_STATUS_FINISHED)
        ) or 0
        if n_before == 0:
            return 0

        self.session.execute(
            delete(Match)
            .where(Match.commence_time < cutoff)
            .where(Match.status != MATCH_STATUS_FINISHED),
        )
        self.session.commit()
        self.log.info(
            "Borrados %d partidos pasados sin resultado (commence_time < %s)",
            n_before, cutoff.isoformat(),
        )
        return n_before
