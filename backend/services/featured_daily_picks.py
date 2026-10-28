"""Picks del día del modelo: generación (reglas v2), congelación y corrección con el marcador.

El día es el civil de Madrid. Los picks se generan **una vez**: en el refresh programado (tras
descargar las cuotas) o la primera vez que se abre la app ese día, lo que llegue antes. Al abrir
la app, si las cuotas de los partidos de hoy tienen más de ``picks_odds_max_age_minutes``, se
actualizan antes (~30 s). Una vez guardados, un trigger de PostgreSQL impide cambiarlos: solo
cambia su corrección (``grading_snapshot``).

La selección (bandas de cuota, combinada…) es pura y está en ``daily_picks_v2``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.app.config import Settings, get_settings
from backend.models import FeaturedDailyPick, League, Match, Odds, Snapshot
from backend.models.match import MATCH_STATUS_SCHEDULED
from backend.services import daily_picks_v2 as dp
from backend.services.graded_odds_service import (
    WINAMAX_SOURCE,
    GradedOddsLine,
    list_graded_winamax_odds,
)
from backend.services.model_calibration_v2 import familia as familia_de_mercado
from backend.services.model_dataset import UEFA_SPORT_KEYS
from backend.services.model_registry import MIN_PARTIDOS_PICK, ModeloActivo, modelo_activo
from backend.services.saved_parlay_grading import (
    cargar_stats_partidos,
    grade_single_leg,
    overall_from_leg_results,
)

MADRID = ZoneInfo("Europe/Madrid")
RULES_VERSION = "v2"
#: Sin versión v2 activa, las P salen del Poisson actual.
MODEL_VERSION_LEGACY = "legacy-v1"
# Alguien espera delante de la pantalla: descargas en paralelo, como el botón "Actualizar cuotas".
FETCH_WORKERS_INTERACTIVO = 4

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Día y ventana
# ---------------------------------------------------------------------------


def dia_de_picks(ahora: datetime) -> date:
    """Día civil de Madrid al que pertenecen los picks generados en ``ahora``."""
    return ahora.astimezone(MADRID).date()


def ventana_del_dia(ahora: datetime, margen_minutos: int) -> tuple[datetime, datetime]:
    """``[ahora + margen, medianoche de Madrid)`` en UTC: partidos que aún pueden ser pick."""
    fin_local = datetime.combine(dia_de_picks(ahora) + timedelta(days=1), time(0), tzinfo=MADRID)
    return ahora + timedelta(minutes=margen_minutos), fin_local.astimezone(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ReglasModelo:
    """Qué líneas del modelo pueden ser pick (por defecto, las del modelo actual sin filtros)."""

    model_version: str = MODEL_VERSION_LEGACY
    #: v2: familias que pasaron la validación (``None`` = todas las de ``MERCADOS_PICKS``).
    familias: frozenset[str] | None = None
    #: v2: partidos mínimos de los dos equipos en los datos del modelo.
    min_partidos: int = 0
    #: v2: exigir que la P esté mezclada con el mercado (si la versión usa mercado).
    exigir_mercado: bool = False


def reglas_del_modelo(modelo: ModeloActivo | None) -> ReglasModelo:
    if modelo is None:
        return ReglasModelo()
    return ReglasModelo(
        model_version=modelo.tag,
        familias=modelo.familias,
        min_partidos=MIN_PARTIDOS_PICK,
        exigir_mercado=modelo.peso_mercado > 0,
    )


def candidatas_desde_lineas(
    lineas: list[GradedOddsLine],
    *,
    desde: datetime,
    hasta: datetime,
    reglas: ReglasModelo | None = None,
) -> list[dp.Candidata]:
    """Líneas que pueden competir: con P del modelo, sin props de jugador, dentro de la ventana.

    Con el modelo v2, además: solo sus líneas, de familias habilitadas, con los dos equipos con
    bastantes partidos, mezcladas con el mercado y de ligas domésticas (UEFA tiene pocos datos).
    """
    r = reglas or ReglasModelo()
    out: list[dp.Candidata] = []
    for ln in lineas:
        p = ln.model_probability
        if p is None or not (Decimal("0") < p < Decimal("1")):
            continue
        if ln.player_name or ln.market_key not in dp.MERCADOS_PICKS or not ln.recommendable:
            continue
        if not (desde <= _aware(ln.commence_time) < hasta):
            continue
        if ln.model_version != r.model_version:
            continue
        if r.model_version != MODEL_VERSION_LEGACY:
            fam = familia_de_mercado(ln.market_key)
            if r.familias is not None and fam not in r.familias:
                continue
            if (ln.partidos_min or 0) < r.min_partidos or ln.league_sport_key in UEFA_SPORT_KEYS:
                continue
            if r.exigir_mercado and not ln.con_mercado:
                continue
        out.append(
            dp.Candidata(
                match_id=ln.match_id,
                home_team=ln.home_team,
                away_team=ln.away_team,
                league=ln.league,
                commence_time=_aware(ln.commence_time),
                market_key=ln.market_key,
                outcome_name=ln.outcome_name,
                outcome_point=ln.outcome_point,
                offered_odds=ln.offered_odds,
                p_model=p,
                lambda_home=ln.lambda_home,
                lambda_away=ln.lambda_away,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def fila_del_dia(session: Session, dia: date) -> FeaturedDailyPick | None:
    return session.scalar(select(FeaturedDailyPick).where(FeaturedDailyPick.pick_date == dia))


def partidos_del_dia(session: Session, desde: datetime, hasta: datetime) -> list[int]:
    """Ids de los partidos de fútbol programados en la ventana."""
    return list(
        session.scalars(
            select(Match.id)
            .join(Match.league)
            .where(Match.commence_time >= desde, Match.commence_time < hasta)
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(~League.sport_key.ilike("%basketball%")),
        ).all()
    )


def ultima_toma_por_partido(session: Session, match_ids: list[int]) -> dict[int, datetime]:
    """Hora de la última toma de cuotas de Winamax de cada partido (los que tienen cuotas)."""
    if not match_ids:
        return {}
    filas = session.execute(
        select(Odds.match_id, func.max(Snapshot.captured_at))
        .join(Snapshot, Snapshot.id == Odds.snapshot_id)
        .where(Snapshot.source == WINAMAX_SOURCE, Odds.match_id.in_(match_ids))
        .group_by(Odds.match_id),
    ).all()
    return {mid: _aware(cap) for mid, cap in filas}


def cuotas_viejas(tomas: dict[int, datetime], ahora: datetime, max_edad_minutos: int) -> bool:
    """``True`` si ningún partido de hoy tiene cuotas o la más vieja supera la edad máxima."""
    if not tomas:
        return True
    return ahora - min(tomas.values()) > timedelta(minutes=max_edad_minutos)


def refrescar_cuotas_de_hoy(session: Session, log: logging.Logger | None = None) -> None:
    """Las mismas descargas que el botón "Actualizar cuotas", solo para las próximas 24 h."""
    from backend.services.winamax_odds_ingester import WinamaxOddsIngester

    WinamaxOddsIngester(
        session, log=log or _log, settings=get_settings(), fetch_workers=FETCH_WORKERS_INTERACTIVO,
    ).ingest_upcoming(
        days_ahead=1,
        max_matches=200,
        only_top5=False,
        sport="football",
        skip_fresh_minutes=15,
        commit=True,
    )


# ---------------------------------------------------------------------------
# Generación (v2)
# ---------------------------------------------------------------------------


def generar_picks_v2(
    session: Session,
    ahora: datetime,
    *,
    settings: Settings | None = None,
    log: logging.Logger | None = None,
) -> FeaturedDailyPick:
    """Calcula y añade a la sesión la fila del día (sin ``commit``)."""
    cfg = settings or get_settings()
    desde, hasta = ventana_del_dia(ahora, cfg.picks_kickoff_margin_minutes)
    horas = max(1, math.ceil((hasta - ahora).total_seconds() / 3600) + 1)
    lineas = list_graded_winamax_odds(
        session, horizon_hours=horas, max_lines=1_000_000, log=log or _log,
    )
    modelo = modelo_activo(session, ahora)
    reglas = reglas_del_modelo(modelo)
    version = reglas.model_version
    cands = candidatas_desde_lineas(lineas, desde=desde, hasta=hasta, reglas=reglas)
    tomas = ultima_toma_por_partido(session, sorted({c.match_id for c in cands}))

    simples, meta = dp.elegir_simples(
        cands,
        odds_min=Decimal(str(cfg.picks_odds_min)),
        odds_max=Decimal(str(cfg.picks_odds_max)),
    )
    meta["candidatas"] = len(cands)
    if modelo is not None:
        meta["modelo"] = {
            "familias": sorted(modelo.familias),
            "peso_mercado": modelo.peso_mercado,
            "partidos_entrenamiento": modelo.fit.n_matches,
        }
    picks_json = [
        dp.pata_a_dict(
            s.candidata,
            model_version=version,
            odds_captured_at=tomas.get(s.candidata.match_id),
            sin_valor=s.sin_valor,
            aviso=s.aviso,
        )
        for s in simples
    ]

    combo_json: dict[str, Any] | None = None
    combo = dp.elegir_combinada(
        cands,
        total_min=Decimal(str(cfg.picks_combo_odds_min)),
        total_max=Decimal(str(cfg.picks_combo_odds_max)),
    )
    if combo is not None:
        combo_json = dp.combinada_a_dict(
            combo,
            model_version=version,
            odds_captured_at=min((tomas[p.match_id] for p in combo.patas if p.match_id in tomas),
                                 default=None),
        )
    else:
        meta["motivo_combinada"] = (
            f"No se llega a una cuota total de {cfg.picks_combo_odds_min:g} a "
            f"{cfg.picks_combo_odds_max:g} ni juntando selecciones de un mismo partido"
        )

    fila = FeaturedDailyPick(
        pick_date=dia_de_picks(ahora),
        picks_json=picks_json,
        combo_json=combo_json,
        rules_version=RULES_VERSION,
        model_version=version,
        generated_at=ahora,
        selection_meta=meta,
        picks_sha256=dp.huella_picks(picks_json, combo_json),
        grading_snapshot=None,
        last_graded_at=None,
    )
    session.add(fila)
    session.flush()
    _refresh_one_row(session, fila)
    return fila


@dataclass(frozen=True)
class PicksDeHoy:
    estado: Literal["listo", "sin_partidos"]
    fila: FeaturedDailyPick | None = None
    motivo: str | None = None
    cuotas_actualizadas: bool = False


def asegurar_picks_de_hoy(
    session: Session,
    ahora: datetime | None = None,
    *,
    actualizar_cuotas: bool = False,
    log: logging.Logger | None = None,
) -> PicksDeHoy:
    """Devuelve los picks de hoy; si aún no existen, los genera y los congela (``commit``).

    ``actualizar_cuotas=True`` (al abrir la app): si las cuotas de hoy están viejas, antes se
    descargan. El refresh programado lo llama sin actualizar, porque acaba de descargarlas.
    Si no queda ningún partido hoy no se guarda nada: se puede volver a intentar más tarde.
    """
    ahora = ahora or datetime.now(timezone.utc)
    dia = dia_de_picks(ahora)
    fila = fila_del_dia(session, dia)
    if fila is not None:
        return PicksDeHoy("listo", fila)

    cfg = get_settings()
    desde, hasta = ventana_del_dia(ahora, cfg.picks_kickoff_margin_minutes)
    ids = partidos_del_dia(session, desde, hasta)
    if not ids:
        return PicksDeHoy("sin_partidos", motivo="Hoy no quedan partidos por empezar")

    actualizadas = False
    if actualizar_cuotas and cuotas_viejas(
        ultima_toma_por_partido(session, ids), ahora, cfg.picks_odds_max_age_minutes,
    ):
        refrescar_cuotas_de_hoy(session, log)
        actualizadas = True

    try:
        fila = generar_picks_v2(session, ahora, settings=cfg, log=log)
        session.commit()
    except IntegrityError:
        # Otra petición (u otro proceso) los acaba de generar: se devuelven esos.
        session.rollback()
        fila = fila_del_dia(session, dia)
    return PicksDeHoy("listo", fila, cuotas_actualizadas=actualizadas)


# ---------------------------------------------------------------------------
# Corrección y métricas
# ---------------------------------------------------------------------------


def _grade_legs(
    legs: list[dict[str, Any]], by_mid: dict[int, Match], stats: dict[int, Any],
) -> list[dict[str, Any]]:
    graded: list[dict[str, Any]] = []
    for lg in legs:
        mid = int(lg["match_id"])
        g = grade_single_leg(dict(lg), by_mid.get(mid), stats.get(mid))
        graded.append({**dict(lg), **g})
    return graded


def _refresh_one_row(session: Session, row: FeaturedDailyPick) -> None:
    """Liquida simples y combinada con los marcadores; solo toca ``grading_snapshot``."""
    combo_legs = list((row.combo_json or {}).get("legs") or [])
    if not row.picks_json and not combo_legs:
        row.grading_snapshot = None
        row.last_graded_at = datetime.now(timezone.utc)
        return

    mids = sorted({int(lg["match_id"]) for lg in [*row.picks_json, *combo_legs]})
    stmt = (
        select(Match)
        .where(Match.id.in_(mids))
        .options(joinedload(Match.home_team), joinedload(Match.away_team))
    )
    by_mid = {m.id: m for m in session.scalars(stmt).all()}
    stats = cargar_stats_partidos(session, by_mid.values())

    snapshot: dict[str, Any] = {"legs": _grade_legs(list(row.picks_json), by_mid, stats)}
    if combo_legs:
        graded_combo = _grade_legs(combo_legs, by_mid, stats)
        snapshot["combo"] = {
            "legs": graded_combo,
            "result": overall_from_leg_results([lg["result"] for lg in graded_combo]),
        }
    row.grading_snapshot = snapshot
    row.last_graded_at = datetime.now(timezone.utc)


def refresh_grading_for_row(session: Session, row: FeaturedDailyPick) -> None:
    _refresh_one_row(session, row)


def partidos_de_picks_recientes(session: Session, ahora: datetime, dias: int = 7) -> set[int]:
    """Partidos de las patas (simples y combinada) de los picks de los últimos ``dias``."""
    desde = dia_de_picks(ahora) - timedelta(days=dias)
    rows = session.scalars(
        select(FeaturedDailyPick).where(FeaturedDailyPick.pick_date >= desde),
    ).all()
    return {
        int(lg["match_id"])
        for r in rows
        for lg in [*r.picks_json, *((r.combo_json or {}).get("legs") or [])]
    }


def refresh_all_grading(session: Session) -> int:
    """Re-evalúa todas las filas con datos de ``matches``. Solo desde "Comprobar" (Transparencia)."""

    rows = session.scalars(
        select(FeaturedDailyPick).order_by(FeaturedDailyPick.pick_date.desc()),
    ).all()
    for r in rows:
        _refresh_one_row(session, r)
    session.commit()
    return len(rows)


def simples_corregidas(row: FeaturedDailyPick) -> list[dict[str, Any]]:
    src = row.grading_snapshot
    if src and isinstance(src.get("legs"), list):
        return list(src["legs"])
    return list(row.picks_json)


def combinada_corregida(row: FeaturedDailyPick) -> dict[str, Any] | None:
    """Combinada guardada con sus patas liquidadas y ``result`` (si ya se corrigió)."""
    if not row.combo_json:
        return None
    graded = (row.grading_snapshot or {}).get("combo")
    if not graded:
        return {**row.combo_json, "result": "pending"}
    return {**row.combo_json, "legs": graded["legs"], "result": graded["result"]}


def _cuenta(resultados: list[str]) -> dict[str, int]:
    hits = sum(r == "hit" for r in resultados)
    misses = sum(r == "miss" for r in resultados)
    return {
        "hits": hits,
        "misses": misses,
        "needs_review": sum(r == "needs_review" for r in resultados),
        "voids": sum(r == "void" for r in resultados),
        # pending y pending_stats (terminado, esperando stats) cuentan como pendientes.
        "pending": sum(r in {"pending", "pending_stats"} for r in resultados),
    }


def _tasa(hits: int, misses: int) -> float | None:
    settled = hits + misses
    return round(1000.0 * hits / settled) / 10.0 if settled > 0 else None


def aggregate_leg_metrics(session: Session) -> dict[str, Any]:
    """Acierto de las simples (todas, con valor y sin valor) y de las combinadas."""

    rows = session.scalars(select(FeaturedDailyPick)).all()
    simples = [lg for row in rows for lg in simples_corregidas(row)]
    res = [(lg.get("result") or "pending") for lg in simples]
    total = _cuenta(res)
    con_valor = _cuenta([r for r, lg in zip(res, simples) if not lg.get("sin_valor")])
    sin_valor = _cuenta([r for r, lg in zip(res, simples) if lg.get("sin_valor")])
    combos = [c for c in (combinada_corregida(row) for row in rows) if c is not None]
    combo = _cuenta([str(c.get("result") or "pending") for c in combos])

    # Acierto por modelo: cada versión se mide por separado (no se mezclan los de legacy-v1).
    por_modelo: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.picks_json and not row.combo_json:
            continue
        m = por_modelo.setdefault(row.model_version or row.rules_version, {"simples": [], "combinadas": []})
        m["simples"].extend((lg.get("result") or "pending") for lg in simples_corregidas(row))
        c = combinada_corregida(row)
        if c is not None:
            m["combinadas"].append(str(c.get("result") or "pending"))
    resumen_modelos = {}
    for version, res_m in por_modelo.items():
        s, c = _cuenta(res_m["simples"]), _cuenta(res_m["combinadas"])
        resumen_modelos[version] = {
            "simples": {**s, "rate_pct": _tasa(s["hits"], s["misses"])},
            "combinadas": {**c, "rate_pct": _tasa(c["hits"], c["misses"])},
        }
    return {
        "total_pick_days": len(rows),
        "total_legs": len(simples),
        **total,
        "rate_pct": _tasa(total["hits"], total["misses"]),
        "value_hits": con_valor["hits"],
        "value_misses": con_valor["misses"],
        "value_rate_pct": _tasa(con_valor["hits"], con_valor["misses"]),
        "novalue_hits": sin_valor["hits"],
        "novalue_misses": sin_valor["misses"],
        "novalue_rate_pct": _tasa(sin_valor["hits"], sin_valor["misses"]),
        "combos_total": len(combos),
        "combo_hits": combo["hits"],
        "combo_misses": combo["misses"],
        "combo_pending": combo["pending"],
        "combo_rate_pct": _tasa(combo["hits"], combo["misses"]),
        "por_modelo": resumen_modelos,
    }


__all__ = [
    "MODEL_VERSION_LEGACY",
    "ReglasModelo",
    "reglas_del_modelo",
    "PicksDeHoy",
    "RULES_VERSION",
    "aggregate_leg_metrics",
    "asegurar_picks_de_hoy",
    "candidatas_desde_lineas",
    "combinada_corregida",
    "cuotas_viejas",
    "dia_de_picks",
    "fila_del_dia",
    "generar_picks_v2",
    "partidos_de_picks_recientes",
    "refresh_all_grading",
    "refresh_grading_for_row",
    "simples_corregidas",
    "ventana_del_dia",
]
