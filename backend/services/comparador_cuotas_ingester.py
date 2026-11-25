"""Ingesta del comparador de cuotas de Flashscore (bet365 y el resto de casas españolas).

Flujo, por partido (una petición ``oce`` cada uno, con pausa entre partidos):

1. Elige los próximos partidos con ``external_id`` de Flashscore que empiezan en los próximos
   ``days_ahead`` días y que no se han leído hace menos de ``REFETCH_AFTER``. Van primero los
   que tienen cuotas de Winamax (son los que se pueden comparar) y, dentro, los más cercanos.
2. Descarga y traduce el comparador (``backend/scrapers/flashscore_cuotas.py``).
3. Crea las casas (``bookmakers``, por ``flashscore_id``) y los mercados que falten y registra
   cada cuota en ``comparador_cuotas`` con la misma lógica que ``odds_history``: fila nueva si
   cambia el precio, si no se alarga ``last_seen_at``.

Cada partido hace commit por separado: un fallo no pierde lo ya descargado. Las cuotas
suspendidas (``active = false``) no se guardan. Tras ``MAX_FALLOS_SEGUIDOS`` partidos fallidos
seguidos se corta la tanda: suele ser un bloqueo o un cambio de formato y seguir solo suma
peticiones.

``ejecutar_paso_comparador`` es el paso del refresh (después de Winamax y antes de los picks),
común a la tarea programada (``backend/tools/import_comparador_cuotas.py``) y al refresh al
abrir la app (``services/refresco_auto.py``). Nunca lanza: los fallos vuelven como errores.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.models import Bookmaker, ComparadorCuota, Market, Match, Odds, Snapshot
from backend.models.bookmaker import BOOKMAKER_ROLE_SOFT
from backend.models.match import MATCH_STATUS_SCHEDULED
from backend.scrapers import flashscore_cuotas as fc
from backend.services.match_absences_ingester import (
    FLASHSCORE_EXTERNAL_PREFIX,
    flashscore_id_from_external_id,
)
from backend.services.odds_history import LastSeen, LineKey, line_key, plan_history_changes

REQUEST_SLEEP_SECONDS = 1.5
#: Con 3 refrescos al día, como mucho una lectura por partido y refresco.
REFETCH_AFTER = timedelta(hours=2)
WINAMAX_SOURCE = "winamax_es"
#: Partidos fallidos seguidos tras los que se corta la tanda (bloqueo, timeout, formato nuevo).
MAX_FALLOS_SEGUIDOS = 3

#: Casas conocidas del comparador (id de Flashscore → clave en ``bookmakers``). Winamax.es
#: reutiliza la fila ``winamax_es`` del scraper propio. Las que no estén aquí se crean con una
#: clave derivada del nombre (``"Retabet"`` → ``retabet_es``).
CASAS_CONOCIDAS: dict[int, tuple[str, str]] = {
    16: ("bet365_es", "bet365 (ES)"),
    883: ("winamax_es", "Winamax (ES)"),
    406: ("sportium_es", "Sportium (ES)"),
    1003: ("1xbet_es", "1xBet (ES)"),
    991: ("versus_es", "Versus (ES)"),
    26: ("betway_es", "Betway (ES)"),
    1121: ("retabet_es", "Retabet (ES)"),
    1: ("interwetten_es", "Interwetten (ES)"),
    1087: ("codere_es", "Codere (ES)"),
    392: ("bwin_es", "bwin (ES)"),
    27: ("888sport_es", "888sport (ES)"),
    526: ("speedybet_es", "Speedybet (ES)"),
    15: ("william_hill_es", "William Hill (ES)"),
}

_NOMBRE_MERCADO = {
    "h2h": "Resultado (1X2)",
    "draw_no_bet": "Empate no válido",
    "asian_handicap": "Hándicap asiático",
    "european_handicap": "Hándicap europeo",
    "correct_score": "Marcador exacto",
    "half_time_full_time": "Descanso / final",
}


def clave_casa(fs_id: int, nombre: str) -> tuple[str, str]:
    """``(key, title)`` de una casa del comparador."""
    if fs_id in CASAS_CONOCIDAS:
        return CASAS_CONOCIDAS[fs_id]
    base = re.sub(r"[^a-z0-9]+", "_", nombre.lower().removesuffix(".es")).strip("_") or str(fs_id)
    return f"{base}_es"[:64], f"{nombre} (ES)"[:128]


def necesita_lectura(
    *, commence_time: datetime, ultima_lectura: datetime | None, now: datetime, days_ahead: int,
) -> bool:
    """Un partido próximo se lee si empieza en la ventana y no se leyó hace menos de 2 h."""
    if not now <= commence_time <= now + timedelta(days=days_ahead):
        return False
    return ultima_lectura is None or now - ultima_lectura >= REFETCH_AFTER


@dataclass
class ComparadorIngestResult:
    started_at: datetime
    finished_at: datetime
    matches_eligible: int = 0
    matches_fetched: int = 0
    cuotas_vistas: int = 0
    filas_nuevas: int = 0
    filas_extendidas: int = 0
    cuotas_por_casa: dict[str, int] = field(default_factory=dict)
    fetch_errors: int = 0
    errors: list[str] = field(default_factory=list)
    #: Se cortó la tanda por ``MAX_FALLOS_SEGUIDOS`` fallos seguidos.
    cortado: bool = False

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class ComparadorCuotasIngester:
    """Descarga y persiste el comparador de cuotas de los próximos partidos."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch: Callable[[str], dict] = fc.fetch_odds_comparison,
        sleep_seconds: float = REQUEST_SLEEP_SECONDS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch = fetch
        self._sleep_seconds = sleep_seconds
        self._casas: dict[int, Bookmaker] = {}
        self._mercados: dict[str, Market] = {}

    def ingest(
        self,
        *,
        days_ahead: int = 3,
        max_matches: int = 8,
        match_ids: list[int] | None = None,
        max_fallos_seguidos: int = MAX_FALLOS_SEGUIDOS,
    ) -> ComparadorIngestResult:
        """Lee el comparador de como mucho ``max_matches`` partidos.

        ``match_ids`` fuerza esos partidos (sin filtro de ventana ni de frescura): es para
        probar a mano con uno o dos.
        """
        started = datetime.now(timezone.utc)
        result = ComparadorIngestResult(started_at=started, finished_at=started)
        if match_ids:
            batch = list(self.session.scalars(select(Match).where(Match.id.in_(match_ids))).all())
            result.matches_eligible = len(batch)
        else:
            eligible = self.partidos_elegibles(now=started, days_ahead=days_ahead)
            result.matches_eligible = len(eligible)
            batch = eligible[:max_matches]
        seguidos = 0
        for i, match in enumerate(batch):
            if i and self._sleep_seconds:
                time.sleep(self._sleep_seconds)
            fs_id = flashscore_id_from_external_id(match.external_id)
            if not fs_id:
                continue
            try:
                payload = self._fetch(fs_id)
                comp = fc.parse_odds_comparison(payload)
                self._guardar(match, comp, result, observed_at=datetime.now(timezone.utc))
                self.session.commit()
            except Exception as exc:  # noqa: BLE001
                self.session.rollback()
                result.fetch_errors += 1
                result.errors.append(f"match={match.id} fs={fs_id}: {exc}")
                self.log.warning("Comparador: error en match=%s fs=%s: %s", match.id, fs_id, exc)
                seguidos += 1
                if seguidos >= max_fallos_seguidos:
                    result.cortado = True
                    self.log.warning(
                        "Comparador: %d fallos seguidos, se corta la tanda (%d partidos sin leer).",
                        seguidos, len(batch) - i - 1,
                    )
                    break
                continue
            seguidos = 0
            result.matches_fetched += 1
        result.finished_at = datetime.now(timezone.utc)
        return result

    # ------------------------------------------------------------------ selección

    def partidos_elegibles(self, *, now: datetime, days_ahead: int) -> list[Match]:
        """Partidos que tocaría leer, en orden (con Winamax primero, luego por hora). Solo BD."""
        candidatos = self.session.scalars(
            select(Match)
            .where(Match.external_id.like(f"{FLASHSCORE_EXTERNAL_PREFIX}%"))
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(Match.commence_time >= now)
            .where(Match.commence_time <= now + timedelta(days=days_ahead))
        ).all()
        if not candidatos:
            return []
        ids = [m.id for m in candidatos]
        ultima = dict(self.session.execute(
            select(ComparadorCuota.match_id, func.max(ComparadorCuota.last_seen_at))
            .where(ComparadorCuota.match_id.in_(ids))
            .group_by(ComparadorCuota.match_id)
        ).all())
        con_winamax = set(self.session.scalars(
            select(Odds.match_id).join(Snapshot, Snapshot.id == Odds.snapshot_id)
            .where(Snapshot.source == WINAMAX_SOURCE, Odds.match_id.in_(ids))
            .distinct()
        ).all())
        elegibles = [
            m for m in candidatos
            if necesita_lectura(
                commence_time=m.commence_time, ultima_lectura=ultima.get(m.id),
                now=now, days_ahead=days_ahead,
            )
        ]
        elegibles.sort(key=lambda m: (m.id not in con_winamax, m.commence_time))
        return elegibles

    # ------------------------------------------------------------------ persistencia

    def _guardar(
        self,
        match: Match,
        comp: fc.ComparadorPartido,
        result: ComparadorIngestResult,
        *,
        observed_at: datetime,
    ) -> None:
        observado_por_casa: dict[int, dict[LineKey, tuple[Decimal, Decimal | None]]] = {}
        for c in comp.cuotas:
            result.cuotas_vistas += 1
            if not c.active:
                continue
            casa = self._casa(c.bookmaker_fs_id, comp.bookmakers.get(c.bookmaker_fs_id, ""))
            mercado = self._mercado(c.market_key)
            key = line_key(match.id, casa.id, mercado.id, None, c.outcome_key[:64], c.line)
            observado_por_casa.setdefault(casa.id, {})[key] = (c.price, c.opening_price)
            result.cuotas_por_casa[casa.key] = result.cuotas_por_casa.get(casa.key, 0) + 1

        for bm_id, observado in observado_por_casa.items():
            ultimas = self._ultimas(match.id, bm_id)
            plan = plan_history_changes(
                ultimas, {k: p for k, (p, _) in observado.items()}, observed_at=observed_at,
            )
            for key, price in plan.to_insert.items():
                _, _, mkt_id, _, outcome, point = key
                self.session.add(ComparadorCuota(
                    match_id=match.id, bookmaker_id=bm_id, market_id=mkt_id,
                    outcome_name=outcome, outcome_point=point, price=price,
                    opening_price=observado[key][1],
                    first_seen_at=observed_at, last_seen_at=observed_at,
                ))
            if plan.to_extend:
                self.session.execute(
                    update(ComparadorCuota)
                    .where(ComparadorCuota.id.in_(plan.to_extend))
                    .values(last_seen_at=observed_at)
                )
            result.filas_nuevas += len(plan.to_insert)
            result.filas_extendidas += len(plan.to_extend)

    def _ultimas(self, match_id: int, bookmaker_id: int) -> dict[LineKey, LastSeen]:
        filas = self.session.scalars(
            select(ComparadorCuota)
            .where(ComparadorCuota.match_id == match_id)
            .where(ComparadorCuota.bookmaker_id == bookmaker_id)
            .order_by(ComparadorCuota.last_seen_at, ComparadorCuota.id)
        ).all()
        return {
            line_key(r.match_id, r.bookmaker_id, r.market_id, None, r.outcome_name,
                     r.outcome_point): LastSeen(r.id, r.price, r.last_seen_at)
            for r in filas
        }

    def _casa(self, fs_id: int, nombre: str) -> Bookmaker:
        if fs_id in self._casas:
            return self._casas[fs_id]
        key, title = clave_casa(fs_id, nombre or str(fs_id))
        casa = self.session.scalar(select(Bookmaker).where(Bookmaker.flashscore_id == fs_id))
        if casa is None:
            casa = self.session.scalar(select(Bookmaker).where(Bookmaker.key == key))
        if casa is None:
            casa = Bookmaker(key=key, title=title, region="es")
            self.session.add(casa)
        casa.flashscore_id = fs_id
        casa.role = casa.role or BOOKMAKER_ROLE_SOFT
        self.session.flush()
        self._casas[fs_id] = casa
        return casa

    def _mercado(self, market_key: str) -> Market:
        if market_key in self._mercados:
            return self._mercados[market_key]
        mercado = self.session.scalar(select(Market).where(Market.key == market_key))
        if mercado is None:
            mercado = Market(key=market_key, name=_NOMBRE_MERCADO.get(market_key, market_key)[:128])
            self.session.add(mercado)
            self.session.flush()
        self._mercados[market_key] = mercado
        return mercado


# ---------------------------------------------------------------------- paso del refresh


@dataclass
class PasoComparador:
    """Lo que devuelve el paso del refresh: el resultado (si llegó a correr) y sus errores."""

    result: ComparadorIngestResult | None = None
    #: Ya con el prefijo ``comparador:``, listos para ``refresh_runs.errores``.
    errores: list[str] = field(default_factory=list)
    #: Apagado con ``COMPARADOR_CUOTAS_ENABLED=false``.
    omitido: bool = False


def errores_del_paso(result: ComparadorIngestResult) -> list[str]:
    """Resume los fallos de una tanda en **una** línea (no uno por partido).

    Con Flashscore bloqueando fallarían todos los partidos y llenarían ``refresh_runs.errores``.
    """
    if not result.fetch_errors:
        return []
    intentados = result.matches_fetched + result.fetch_errors
    linea = f"comparador: {result.fetch_errors} de {intentados} partidos fallaron"
    if result.cortado:
        linea += f" (cortado tras {MAX_FALLOS_SEGUIDOS} seguidos)"
    if result.errors:
        linea += f"; primero: {result.errors[0]}"
    return [linea[:500]]


def ejecutar_paso_comparador(
    session: Session,
    *,
    log: logging.Logger,
    settings: Settings | None = None,
    days_ahead: int | None = None,
    max_matches: int | None = None,
    ingester_factory: Callable[..., ComparadorCuotasIngester] | None = None,
) -> PasoComparador:
    """Paso «comparador de cuotas» del refresh. **Nunca lanza**: un fallo no corta el pipeline.

    ``days_ahead`` y ``max_matches`` salen de la configuración si no se pasan
    (``COMPARADOR_CUOTAS_DAYS_AHEAD`` y ``COMPARADOR_CUOTAS_MAX_PARTIDOS``).
    """
    try:
        settings = settings or get_settings()
        if not settings.comparador_cuotas_enabled:
            log.info("Comparador de cuotas: apagado (COMPARADOR_CUOTAS_ENABLED=false).")
            return PasoComparador(omitido=True)
        dias = settings.comparador_cuotas_days_ahead if days_ahead is None else days_ahead
        tope = settings.comparador_cuotas_max_partidos if max_matches is None else max_matches
        log.info("Comparador de cuotas (próximos %d días, como mucho %d partidos)...", dias, tope)
        factory = ingester_factory or ComparadorCuotasIngester
        res = factory(session, log=log).ingest(days_ahead=dias, max_matches=tope)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("Comparador de cuotas: fallo")
        return PasoComparador(errores=[f"comparador: {exc}"[:500]])
    log.info(
        "Comparador de cuotas: %d de %d partidos elegibles leídos, %d filas nuevas, "
        "%d alargadas, %d fallidos%s (%.1fs).",
        res.matches_fetched, res.matches_eligible, res.filas_nuevas, res.filas_extendidas,
        res.fetch_errors, ", tanda cortada" if res.cortado else "", res.duration_seconds,
    )
    return PasoComparador(result=res, errores=errores_del_paso(res))
