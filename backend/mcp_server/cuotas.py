"""Herramientas de cuotas, picks y apuestas: estado de los datos, partidos, líneas con valor,
previa del partido, picks de hoy, historial, transparencia y «Mis apuestas».

Reutilizan los mismos servicios que la API (``list_graded_winamax_odds``,
``consultar_picks_de_hoy``, ``aggregate_leg_metrics``, ``resumen_apuestas``…). Nada de aquí
genera picks, refresca cuotas ni sale a la red: la previa monta la alineación con un
``fetch_players`` que falla siempre, así que se queda con la jugada, la oficial ya guardada o
la probable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.mcp_server import formato as f
from backend.mcp_server.estadisticas import CLAVES_MEDIAS, puntuar_equipo
from backend.models import AppUser, FeaturedDailyPick, MatchStats, SavedParlay, Snapshot, Team
from backend.services.daily_picks_v2 import es_menos_goles
from backend.services.featured_daily_picks import (
    aggregate_leg_metrics,
    combinada_corregida,
    consultar_picks_de_hoy,
    simples_corregidas,
)
from backend.services.graded_odds_service import WINAMAX_SOURCE, GradedOddsLine, list_graded_winamax_odds
from backend.services.match_lineups import MatchLineupService
from backend.services.match_listing import latest_1x2_by_match, listar_partidos
from backend.services.match_preview import MatchPreviewService, TeamAverages, TeamPreview
from backend.services.model_registry import version_activa
from backend.services.refresco_auto import historial_de_refresh
from backend.services.saved_parlays import beneficio_apuesta, resumen_apuestas

log = logging.getLogger(__name__)

AVISO = f.AVISO_JUEGO_RESPONSABLE

# ---------------------------------------------------------------------------
# Estado de los datos
# ---------------------------------------------------------------------------


def estado_datos(session: Session) -> dict[str, Any]:
    h = historial_de_refresh(session)
    cuotas_de = session.scalar(
        select(func.max(Snapshot.captured_at)).where(Snapshot.source == WINAMAX_SOURCE)
    )
    stats_de = session.scalar(select(func.max(MatchStats.captured_at)))
    modelo = version_activa(session)
    return f.sin_vacios({
        "ultimo_refresco_inicio": f.fmt_fecha(h.ultimo_inicio),
        "ultimo_refresco_fin": f.fmt_fecha(h.ultimo_fin),
        "ultimo_refresco_ok": h.ultimo_ok,
        "errores_ultimo_refresco": [f.sanear(e, 160) for e in h.ultimo_errores[:5]] or None,
        "calendario_vacio": h.calendario_vacio or None,
        "cuotas_winamax_de": f.fmt_fecha(cuotas_de),
        "stats_de_partido_de": f.fmt_fecha(stats_de),
        "modelo": {
            "version": modelo.tag, "desde": f.fmt_fecha(modelo.promoted_at),
        } if modelo else {"version": "legacy-v1"},
        "ahora": f.fmt_fecha(datetime.now(timezone.utc)),
    })


# ---------------------------------------------------------------------------
# Partidos
# ---------------------------------------------------------------------------


def buscar_partidos(
    session: Session, texto: str | None = None, liga: str | None = None,
    dias: int = 3, dias_atras: int = 0, limite: int = 20,
) -> dict[str, Any]:
    dias, dias_atras, limite = f.acotar(dias, 0, 14), f.acotar(dias_atras, 0, 7), f.acotar(limite, 1, 50)
    team_ids = None
    if texto:
        equipos = session.execute(select(Team.id, Team.name)).all()
        puntos = [(puntuar_equipo(texto, n), tid) for tid, n in equipos]
        mejor = max((p for p, _ in puntos), default=0)
        team_ids = [tid for p, tid in puntos if p >= max(1, min(mejor, 2))]
        if not team_ids:
            return {"buscado": f.sanear(texto), "partidos": [],
                    "aviso": "Ningún equipo encaja con ese texto."}
    partidos = listar_partidos(
        session, league=liga, days_ahead=max(dias, 0) or 1, days_back=dias_atras,
        limit=limite + 1, team_ids=team_ids,
    )
    partidos, truncado = f.recortar(partidos, limite)
    unoxdos = latest_1x2_by_match(session, partidos)
    filas = []
    for m in partidos:
        o = unoxdos.get(m.id)
        filas.append(f.sin_vacios({
            "match_id": m.id,
            "fecha": f.fmt_fecha(m.commence_time),
            "liga": f.sanear(m.league.name),
            "partido": f.texto_partido(m.home_team.name, m.away_team.name),
            "estado": m.status,
            "marcador": f"{m.home_score}-{m.away_score}" if m.home_score is not None else None,
            "1x2": [f.fmt_cuota(o.home), f.fmt_cuota(o.draw), f.fmt_cuota(o.away)] if o else None,
        }))
    out: dict[str, Any] = {"partidos": filas}
    if truncado:
        out["truncado"] = True
    return out


# ---------------------------------------------------------------------------
# Cuotas con valor
# ---------------------------------------------------------------------------


def linea_compacta(ln: GradedOddsLine, *, con_resumen: bool = False) -> dict[str, Any]:
    d = {
        "match_id": ln.match_id,
        "partido": f.texto_partido(ln.home_team, ln.away_team),
        "fecha": f.fmt_fecha(ln.commence_time),
        "liga": f.sanear(ln.league),
        "mercado": ln.market_key,
        "seleccion": f.texto_seleccion(ln.outcome_name, ln.outcome_point, ln.player_name),
        "cuota": f.fmt_cuota(ln.offered_odds),
        "p_modelo": f.fmt_prob(ln.model_probability),
        "p_implicita": f.fmt_prob(ln.implied_probability),
        "ev": f.fmt_ev(ln.model_ev),
        "confianza": ln.confidence,
        "recomendable": ln.recommendable,
    }
    if con_resumen and ln.model_summary_lines:
        d["resumen"] = [f.sanear(x, 240) for x in ln.model_summary_lines[:3]]
    return f.sin_vacios(d)


def filtrar_lineas(
    lineas: list[GradedOddsLine], *, mercado: str | None = None, ev_min: float = 0.0,
    solo_recomendables: bool = True, incluir_menos_goles: bool = False,
) -> list[GradedOddsLine]:
    """Líneas con EV del modelo ≥ ``ev_min`` (fracción), ordenadas de más a menos EV."""
    umbral = Decimal(str(ev_min))
    out = []
    for ln in lineas:
        if ln.model_ev is None or ln.model_ev < umbral:
            continue
        if solo_recomendables and not ln.recommendable:
            continue
        if mercado and f.grupo_amplio(ln.market_key) != mercado:
            continue
        if not incluir_menos_goles and es_menos_goles(ln.market_key, ln.outcome_name):
            continue
        out.append(ln)
    out.sort(key=lambda ln: ln.model_ev, reverse=True)
    return out


def cuotas_con_valor(
    session: Session, horas: int = 48, liga: str | None = None, mercado: str | None = None,
    ev_min: float = 0.0, solo_recomendables: bool = True, incluir_menos_goles: bool = False,
    limite: int = 20,
) -> dict[str, Any]:
    horas, limite = f.acotar(horas, 1, 120), f.acotar(limite, 1, 50)
    if mercado and mercado not in f.GRUPOS_DE_MERCADO:
        return {"error": f"mercado debe ser uno de {', '.join(f.GRUPOS_DE_MERCADO)}"}
    todas = list_graded_winamax_odds(session, horizon_hours=horas, sport_key=liga, log=log)
    elegidas = filtrar_lineas(
        todas, mercado=mercado, ev_min=ev_min, solo_recomendables=solo_recomendables,
        incluir_menos_goles=incluir_menos_goles,
    )
    elegidas, truncado = f.recortar(elegidas, limite)
    out: dict[str, Any] = {
        "lineas_revisadas": len(todas),
        "lineas": [linea_compacta(ln) for ln in elegidas],
        "criterio": f"EV del modelo ≥ {f.fmt_ev(ev_min)}, próximas {horas} h"
                    + ("" if incluir_menos_goles else ", sin «menos goles» ni «ambos marcan: No»"),
        "aviso": AVISO,
    }
    if truncado:
        out["truncado"] = True
    if not todas:
        out["nota"] = "No hay cuotas de Winamax en ese horizonte (¿falta refrescar cuotas?)."
    return out


def lineas_de_partido(
    session: Session, match_id: int, mercado: str | None = None, limite: int = 30,
) -> dict[str, Any]:
    limite = f.acotar(limite, 1, 60)
    if mercado and mercado not in f.GRUPOS_DE_MERCADO:
        return {"error": f"mercado debe ser uno de {', '.join(f.GRUPOS_DE_MERCADO)}"}
    lineas = list_graded_winamax_odds(session, horizon_hours=24 * 14, match_id=match_id, log=log)
    if not lineas:
        return {"match_id": match_id, "lineas": [],
                "aviso": "Este partido no tiene cuotas de Winamax próximas en la BD "
                         "(o ya ha empezado)."}
    if mercado:
        lineas = [ln for ln in lineas if f.grupo_amplio(ln.market_key) == mercado]
    lineas.sort(key=lambda ln: (ln.model_ev is None, -(ln.model_ev or 0)))
    primera = lineas[0] if lineas else None
    lineas, truncado = f.recortar(lineas, limite)
    out: dict[str, Any] = {
        "match_id": match_id,
        "partido": f.texto_partido(primera.home_team, primera.away_team) if primera else None,
        "fecha": f.fmt_fecha(primera.commence_time) if primera else None,
        "lambda": f.sin_vacios({
            "local": f.fmt_num(primera.lambda_home), "visitante": f.fmt_num(primera.lambda_away),
        }) if primera else None,
        "version_modelo": primera.model_version if primera else None,
        "lineas": [linea_compacta(ln, con_resumen=True) for ln in lineas],
        "aviso": AVISO,
    }
    if truncado:
        out["truncado"] = True
    return f.sin_vacios(out)


# ---------------------------------------------------------------------------
# Previa del partido (sin red)
# ---------------------------------------------------------------------------


def _sin_red(_flashscore_id: str) -> dict:
    """``fetch_players`` para la previa: el MCP nunca sale a Flashscore."""
    raise RuntimeError("el servidor MCP no sale a la red")


def _medias_previa(av: TeamAverages) -> dict[str, Any]:
    d = {"partidos": av.matches, "goles_contra": f.fmt_num(av.goals_against)}
    d.update({clave: f.fmt_num(getattr(av, campo)) for campo, clave in CLAVES_MEDIAS.items()})
    return f.sin_vacios(d)


def _equipo_previa(t: TeamPreview, *, en_casa: bool) -> dict[str, Any]:
    return f.sin_vacios({
        "equipo_id": t.team_id,
        "equipo": f.sanear(t.name),
        "medias_temporada": _medias_previa(t.overall),
        ("medias_en_casa" if en_casa else "medias_fuera"): _medias_previa(t.venue),
        "forma": f.forma_texto([x.result for x in t.form]),
        "ultimos": [
            {"fecha": f.fmt_fecha(x.commence_time), "rival": f.sanear(x.opponent),
             "campo": "casa" if x.is_home else "fuera",
             "marcador": f"{x.goals_for}-{x.goals_against}"}
            for x in t.form
        ],
        "bajas": [
            f.sin_vacios({"jugador": f.sanear(a.name),
                          "estado": "baja" if a.status == "missing" else "duda",
                          "motivo": f.sanear(a.reason)})
            for a in t.absences
        ],
        "once": f.sin_vacios({
            "tipo": t.lineup.kind,
            "segun": f.sanear(t.lineup.based_on),
            "jugadores": [
                f.sanear(p.name) + (f" ({'baja' if p.status == 'missing' else 'duda'})" if p.status else "")
                for p in t.lineup.starters
            ],
        }) if t.lineup.kind != "none" else None,
        "jugadores_clave": [
            f.sin_vacios({
                "jugador": f.sanear(p.name), "pos": p.position, "pj": p.apps, "min": p.minutes,
                "goles": p.goals, "tiros_p90": f.fmt_num(p.shots_per90),
                "puerta_p90": f.fmt_num(p.shots_on_target_per90),
            })
            for p in t.players[:8]
        ],
    })


def previa_partido(session: Session, match_id: int) -> dict[str, Any]:
    lineups = MatchLineupService(session, fetch_players=_sin_red, log=log)
    p = MatchPreviewService(session, lineups=lineups).build(match_id)
    if p is None:
        return {"error": f"No hay partido con id {match_id}. Búscalo con buscar_partidos."}
    return f.sin_vacios({
        "match_id": p.match_id,
        "liga": f.sanear(p.league_name),
        "fecha": f.fmt_fecha(p.commence_time),
        "estado": p.status,
        "marcador": f"{p.home_score}-{p.away_score}" if p.home_score is not None else None,
        "cuotas_de": f.fmt_fecha(p.odds_updated_at),
        "local": _equipo_previa(p.home, en_casa=True),
        "visitante": _equipo_previa(p.away, en_casa=False),
        "notas": "Medias por partido de esta temporada (todas las competiciones). Once: "
                 "played = jugado, official = oficial guardada, probable = titulares del "
                 "último partido.",
    })


# ---------------------------------------------------------------------------
# Picks del día, historial y transparencia
# ---------------------------------------------------------------------------


def _fila_picks(row: FeaturedDailyPick) -> dict[str, Any]:
    return f.sin_vacios({
        "dia": f.fmt_dia(row.pick_date),
        "modelo": row.model_version or row.rules_version,
        "generado": f.fmt_fecha(row.generated_at),
        "simples": [f.pata_compacta(lg) for lg in simples_corregidas(row)],
        "combinada": f.combinada_compacta(combinada_corregida(row)),
    })


def picks_de_hoy(session: Session) -> dict[str, Any]:
    res = consultar_picks_de_hoy(session)
    if res is None:
        return {
            "estado": "pendiente",
            "aviso": "Hoy aún no se han generado los picks. Se generan al abrir la app o en la "
                     "tarea programada; desde el chat no se generan.",
        }
    if res.estado == "sin_partidos":
        return {"estado": "sin_partidos", "motivo": res.motivo}
    return {"estado": "listo", **_fila_picks(res.fila), "aviso": AVISO}


def historial_picks(session: Session, dias: int = 14) -> dict[str, Any]:
    dias = f.acotar(dias, 1, 60)
    filas = session.scalars(
        select(FeaturedDailyPick).order_by(FeaturedDailyPick.pick_date.desc()).limit(dias)
    ).all()
    return {"dias": [_fila_picks(r) for r in filas], "aviso": AVISO}


def _bloque_metricas(d: dict[str, Any]) -> dict[str, Any]:
    return f.sin_vacios({
        "aciertos": d.get("hits"), "fallos": d.get("misses"), "pendientes": d.get("pending"),
        "nulas": d.get("voids") or None, "a_revisar": d.get("needs_review") or None,
        "acierto": f.fmt_pct_directo(d.get("rate_pct")),
    })


def transparencia(session: Session) -> dict[str, Any]:
    m = aggregate_leg_metrics(session)
    return f.sin_vacios({
        "dias_con_picks": m["total_pick_days"],
        "simples": {
            "total": m["total_legs"], **_bloque_metricas(m),
            "con_valor": _bloque_metricas({
                "hits": m["value_hits"], "misses": m["value_misses"], "rate_pct": m["value_rate_pct"],
            }),
            "sin_valor": _bloque_metricas({
                "hits": m["novalue_hits"], "misses": m["novalue_misses"],
                "rate_pct": m["novalue_rate_pct"],
            }),
        },
        "combinadas": {
            "total": m["combos_total"], **_bloque_metricas({
                "hits": m["combo_hits"], "misses": m["combo_misses"],
                "pending": m["combo_pending"], "rate_pct": m["combo_rate_pct"],
            }),
        },
        "por_modelo": {
            version: {"simples": _bloque_metricas(v["simples"]),
                      "combinadas": _bloque_metricas(v["combinadas"])}
            for version, v in (m.get("por_modelo") or {}).items()
        },
        "notas": "Las mismas cifras que la página Transparencia (todas las fechas; sin filtro "
                 "por mercado ni periodo).",
    })


# ---------------------------------------------------------------------------
# Mis apuestas
# ---------------------------------------------------------------------------

ESTADOS_APUESTA = {
    "pendiente": ("pending", "pending_stats"),
    "acertada": ("hit",),
    "fallada": ("miss",),
    "nula": ("void",),
    "a_revisar": ("needs_review",),
}


def _usuario(session: Session) -> AppUser | str:
    nombre = (get_settings().chat_usuario or "").strip()
    if nombre:
        u = session.scalar(select(AppUser).where(AppUser.username == nombre))
        return u or f"No existe el usuario «{nombre}» (CHAT_USUARIO)."
    usuarios = session.scalars(select(AppUser).limit(2)).all()
    if len(usuarios) == 1:
        return usuarios[0]
    return ("No hay usuarios." if not usuarios
            else "Hay varios usuarios: fija CHAT_USUARIO en el .env para elegir uno.")


def mis_apuestas(session: Session, estado: str | None = None, limite: int = 20) -> dict[str, Any]:
    limite = f.acotar(limite, 1, 50)
    if estado and estado not in ESTADOS_APUESTA:
        return {"error": f"estado debe ser uno de {', '.join(ESTADOS_APUESTA)}"}
    u = _usuario(session)
    if isinstance(u, str):
        return {"error": u}
    filas = list(session.scalars(
        select(SavedParlay).where(SavedParlay.user_id == u.id).order_by(SavedParlay.id.desc())
    ).all())
    r = resumen_apuestas(filas)
    elegidas = [x for x in filas if not estado or x.overall_result in ESTADOS_APUESTA[estado]]
    elegidas, truncado = f.recortar(elegidas, limite)
    patas = lambda x: (x.grading_snapshot or {}).get("legs") or x.legs_snapshot  # noqa: E731
    out: dict[str, Any] = {
        "resumen": f.sin_vacios({
            "total": r["total"], "pendientes": r["pending"], "aciertos": r["hits"],
            "fallos": r["misses"], "nulas": r["voids"] or None,
            "a_revisar": r["needs_review"] or None,
            "acierto": f.fmt_pct_directo(r["rate_pct"]),
            "apostado": f.fmt_euros(r["staked"]), "beneficio": f.fmt_euros(r["profit"], signo=True),
            "roi": f.fmt_pct_directo(r["roi_pct"]),
        }),
        "apuestas": [
            f.sin_vacios({
                "id": x.id,
                "fecha": f.fmt_fecha(x.created_at),
                "titulo": f.sanear(x.title),
                "cuota": f.fmt_cuota(x.combined_odds),
                "importe": f.fmt_euros(x.stake),
                "resultado": f.resultado_es(x.overall_result),
                "beneficio": f.fmt_euros(
                    beneficio_apuesta(x.overall_result, x.stake, x.combined_odds), signo=True,
                ),
                "patas": [f.pata_compacta(lg) for lg in patas(x)],
            })
            for x in elegidas
        ],
        "aviso": AVISO,
    }
    if truncado:
        out["truncado"] = True
    return out


__all__ = [
    "buscar_partidos",
    "cuotas_con_valor",
    "estado_datos",
    "filtrar_lineas",
    "historial_picks",
    "linea_compacta",
    "lineas_de_partido",
    "mis_apuestas",
    "picks_de_hoy",
    "previa_partido",
    "transparencia",
]
