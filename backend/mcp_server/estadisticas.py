"""Herramientas de estadísticas: equipos, jugadores, partidos jugados, clasificación y bajas.

Todo sale de lo que ya scrapea el pipeline (Flashscore): ``match_stats`` por equipo y partido,
``player_match_stats`` por jugador y partido, ``team_season_stats`` (clasificación) y
``match_absences``. Las medias de equipo reutilizan ``match_preview.average`` (las mismas que
enseña la página del partido). La temporada empieza el 1 de julio (``season_start``).

Las búsquedas por nombre reutilizan ``team_name_matching`` y **nunca adivinan**: devuelven la
lista de candidatos con su id y el chat elige o pregunta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from backend.mcp_server import formato as f
from backend.models import (
    League,
    Match,
    MatchAbsence,
    MatchStats,
    Player,
    PlayerMatchStats,
    Season,
    Team,
    TeamSeasonStats,
)
from backend.models.match import MATCH_STATUS_FINISHED, MATCH_STATUS_SCHEDULED
from backend.services.match_preview import (
    TeamAverages,
    TeamSample,
    average,
    result_letter,
    season_start,
)
from backend.services.team_name_matching import (
    normalize_team_name,
    player_name_key,
    player_names_match,
    team_names_match,
    team_side_score,
)

MAX_CANDIDATOS = 15
AVISO_VARIOS = "Hay varios candidatos: no elijas tú, pregunta al usuario cuál es (por su id)."

# ---------------------------------------------------------------------------
# Búsqueda por nombre (puras)
# ---------------------------------------------------------------------------

COINCIDENCIA = {3: "exacta", 2: "probable", 1: "posible"}


def puntuar_equipo(texto: str, nombre: str) -> int:
    """3 = mismo nombre normalizado, 2 = mismo club o contenido, 1 = indicio, 0 = nada."""
    a, b = normalize_team_name(texto), normalize_team_name(nombre)
    if not a or not b:
        return 0
    if a == b:
        return 3
    # Cada palabra buscada empieza una palabra del nombre: "Real" → "Real Madrid", no "Villarreal".
    prefijos = all(any(tb.startswith(ta) for tb in b.split()) for ta in a.split())
    if team_names_match(texto, nombre) or prefijos:
        return 2
    return 1 if team_side_score(texto, nombre) >= 1 else 0


def puntuar_jugador(texto: str, nombre: str) -> int:
    """3 = mismas palabras, 2 = encaja (``player_names_match``), 1 = alguna palabra empieza igual."""
    ka, kb = player_name_key(texto), player_name_key(nombre)
    if not ka or not kb:
        return 0
    if ka == kb:
        return 3
    if player_names_match(texto, nombre):
        return 2
    largas = [t for t in ka if len(t) >= 3]
    return 1 if any(n.startswith(t) for t in largas for n in kb) else 0


def mejores_candidatos(
    puntuados: list[tuple[int, str, dict[str, Any]]], limite: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Ordena ``(puntos, nombre, fila)`` y, si hay alguno claro (≥2), descarta los indicios."""
    utiles = [p for p in puntuados if p[0] > 0]
    if any(p[0] >= 2 for p in utiles):
        utiles = [p for p in utiles if p[0] >= 2]
    utiles.sort(key=lambda p: (-p[0], p[1]))
    filas = [{**fila, "coincidencia": COINCIDENCIA[p]} for p, _, fila in utiles]
    return f.recortar(filas, limite)


def _respuesta_candidatos(texto: str, filas: list[dict[str, Any]], truncado: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"buscado": f.sanear(texto), "candidatos": filas}
    if not filas:
        out["aviso"] = "Sin coincidencias en la BD. Prueba con otra forma del nombre."
    elif len(filas) > 1 and not (filas[0]["coincidencia"] == "exacta"
                                 and filas[1]["coincidencia"] != "exacta"):
        out["aviso"] = AVISO_VARIOS
    if truncado:
        out["truncado"] = True
    return out


def _liga_por_equipo(session: Session, team_ids: list[int]) -> dict[int, str]:
    """Liga del partido más reciente de cada equipo (para distinguir homónimos)."""
    if not team_ids:
        return {}
    out: dict[int, str] = {}
    filas = session.execute(
        select(Match.home_team_id, Match.away_team_id, League.name)
        .join(League, League.id == Match.league_id)
        .where(or_(Match.home_team_id.in_(team_ids), Match.away_team_id.in_(team_ids)))
        .where(~League.sport_key.like("soccer_uefa%"))
        .order_by(Match.commence_time.desc())
    ).all()
    for h, a, liga in filas:
        for tid in (h, a):
            if tid in team_ids and tid not in out:
                out[tid] = liga
    return out


def buscar_equipo(session: Session, texto: str, limite: int = 10) -> dict[str, Any]:
    limite = f.acotar(limite, 1, MAX_CANDIDATOS)
    equipos = session.execute(select(Team.id, Team.name)).all()
    puntuados = [
        (puntuar_equipo(texto, nombre), nombre, {"equipo_id": tid, "nombre": f.sanear(nombre)})
        for tid, nombre in equipos
    ]
    filas, truncado = mejores_candidatos(puntuados, limite)
    ligas = _liga_por_equipo(session, [x["equipo_id"] for x in filas])
    for x in filas:
        if x["equipo_id"] in ligas:
            x["liga"] = f.sanear(ligas[x["equipo_id"]])
    return _respuesta_candidatos(texto, filas, truncado)


def buscar_jugador(
    session: Session, texto: str, equipo_id: int | None = None, limite: int = 10,
) -> dict[str, Any]:
    limite = f.acotar(limite, 1, MAX_CANDIDATOS)
    stmt = select(Player.id, Player.name, Player.position, Team.name).outerjoin(
        Team, Team.id == Player.team_id,
    )
    if equipo_id is not None:
        stmt = stmt.where(Player.team_id == equipo_id)
    puntuados = [
        (
            puntuar_jugador(texto, nombre),
            nombre,
            f.sin_vacios({
                "jugador_id": pid, "nombre": f.sanear(nombre),
                "posicion": pos, "equipo": f.sanear(equipo),
            }),
        )
        for pid, nombre, pos, equipo in session.execute(stmt).all()
    ]
    filas, truncado = mejores_candidatos(puntuados, limite)
    return _respuesta_candidatos(texto, filas, truncado)


# ---------------------------------------------------------------------------
# Equipo
# ---------------------------------------------------------------------------

#: Campo de ``TeamAverages`` → clave de la respuesta.
CLAVES_MEDIAS = {
    "goals_for": "goles",
    "xg": "xg",
    "shots": "tiros",
    "shots_on_target": "tiros_puerta",
    "corners": "corners",
    "yellow_cards": "amarillas",
    "goals_1h": "goles_1t",
    "goals_2h": "goles_2t",
    "corners_1h": "corners_1t",
    "corners_2h": "corners_2t",
    "cards_1h": "amarillas_1t",
    "cards_2h": "amarillas_2t",
}


def medias_compactas(propias: TeamAverages, rival: TeamAverages) -> dict[str, Any]:
    """Medias por partido «a favor» (del equipo) y «en contra» (de sus rivales)."""
    def bloque(av: TeamAverages) -> dict[str, Any]:
        return f.sin_vacios({clave: f.fmt_num(getattr(av, campo)) for campo, clave in CLAVES_MEDIAS.items()})

    return {"partidos": propias.matches, "a_favor": bloque(propias), "en_contra": bloque(rival)}


def _muestra(st: MatchStats | None, *, is_home: bool, gf: int, gc: int) -> TeamSample:
    def n(v: Any) -> float | None:
        return float(v) if v is not None else None

    return TeamSample(
        is_home=is_home, goals_for=gf, goals_against=gc,
        xg=n(st.xg) if st else None,
        shots=n(st.shots_total) if st else None,
        shots_on_target=n(st.shots_on_target) if st else None,
        corners=n(st.corners) if st else None,
        yellow_cards=n(st.yellow_cards) if st else None,
        goals_1h=n(st.goals_1h) if st else None,
        goals_2h=n(st.goals_2h) if st else None,
        corners_1h=n(st.corners_1h) if st else None,
        corners_2h=n(st.corners_2h) if st else None,
        cards_1h=n(st.yellow_cards_1h) if st else None,
        cards_2h=n(st.yellow_cards_2h) if st else None,
    )


@dataclass(frozen=True)
class PartidoJugado:
    """Un partido terminado visto desde un equipo: su muestra, la del rival y el resumen."""

    match_id: int
    fecha: datetime
    liga: str
    rival: str
    en_casa: bool
    goles_favor: int
    goles_contra: int
    propia: TeamSample
    del_rival: TeamSample


def partidos_jugados(
    session: Session, equipo_id: int, *, desde: datetime | None = None,
    rival_id: int | None = None, limite: int | None = None,
) -> list[PartidoJugado]:
    """Partidos terminados del equipo (del más reciente al más antiguo) con las stats de ambos."""
    propia, ajena = aliased(MatchStats), aliased(MatchStats)
    stmt = (
        select(Match, propia, ajena)
        .outerjoin(propia, (propia.match_id == Match.id) & (propia.team_id == equipo_id))
        .outerjoin(ajena, (ajena.match_id == Match.id) & (ajena.team_id != equipo_id))
        .where(or_(Match.home_team_id == equipo_id, Match.away_team_id == equipo_id))
        .where(Match.status == MATCH_STATUS_FINISHED)
        .where(Match.home_score.is_not(None))
        .options(joinedload(Match.home_team), joinedload(Match.away_team), joinedload(Match.league))
        .order_by(Match.commence_time.desc())
    )
    if desde is not None:
        stmt = stmt.where(Match.commence_time >= desde)
    if rival_id is not None:
        stmt = stmt.where(or_(Match.home_team_id == rival_id, Match.away_team_id == rival_id))
    if limite is not None:
        stmt = stmt.limit(limite)
    out: list[PartidoJugado] = []
    for m, st, st_rival in session.execute(stmt).all():
        casa = m.home_team_id == equipo_id
        gf = m.home_score if casa else m.away_score
        gc = m.away_score if casa else m.home_score
        out.append(PartidoJugado(
            match_id=m.id, fecha=m.commence_time, liga=m.league.name,
            rival=(m.away_team if casa else m.home_team).name, en_casa=casa,
            goles_favor=gf, goles_contra=gc,
            propia=_muestra(st, is_home=casa, gf=gf, gc=gc),
            del_rival=_muestra(st_rival, is_home=not casa, gf=gc, gc=gf),
        ))
    return out


def medias_de(partidos: list[PartidoJugado]) -> dict[str, Any]:
    return medias_compactas(average([p.propia for p in partidos]), average([p.del_rival for p in partidos]))


def linea_partido(p: PartidoJugado) -> dict[str, Any]:
    """Un partido de la forma: ``{"fecha", "rival", "campo", "marcador", "res"}``."""
    st = p.propia
    out = {
        "match_id": p.match_id,
        "fecha": f.fmt_fecha(p.fecha),
        "rival": f.sanear(p.rival),
        "campo": "casa" if p.en_casa else "fuera",
        "marcador": f"{p.goles_favor}-{p.goles_contra}",
        "res": f.forma_texto([result_letter(p.goles_favor, p.goles_contra)]),
        "corners": f.fmt_num(st.corners, 0),
        "corners_rival": f.fmt_num(p.del_rival.corners, 0),
        "amarillas": f.fmt_num(st.yellow_cards, 0),
        "tiros_puerta": f.fmt_num(st.shots_on_target, 0),
        "xg": f.fmt_num(st.xg),
    }
    return f.sin_vacios(out)


def balance(partidos: list[PartidoJugado]) -> dict[str, int]:
    letras = [result_letter(p.goles_favor, p.goles_contra) for p in partidos]
    return {
        "v": letras.count("W"), "e": letras.count("D"), "d": letras.count("L"),
        "gf": sum(p.goles_favor for p in partidos), "gc": sum(p.goles_contra for p in partidos),
    }


def _equipo(session: Session, equipo_id: int) -> Team | None:
    return session.get(Team, equipo_id)


def _no_existe(que: str, ident: int) -> dict[str, Any]:
    return {"error": f"No hay {que} con id {ident}. Búscalo antes por nombre para tener su id."}


def _temporada_equipo(session: Session, equipo_id: int) -> list[dict[str, Any]]:
    """Fila de clasificación de la temporada más reciente (una por liga con datos)."""
    filas = session.execute(
        select(TeamSeasonStats, Season, League)
        .join(Season, Season.id == TeamSeasonStats.season_id)
        .join(League, League.id == Season.league_id)
        .where(TeamSeasonStats.team_id == equipo_id)
        .order_by(Season.year_start.desc())
    ).all()
    if not filas:
        return []
    ultimo = filas[0][1].year_start
    out = []
    for ts, season, league in filas:
        if season.year_start != ultimo:
            continue
        out.append(f.sin_vacios({
            "liga": f.sanear(league.name), "temporada": season.name,
            "pj": ts.matches_played, "v": ts.wins, "e": ts.draws, "d": ts.losses,
            "gf": ts.goals_for, "gc": ts.goals_against, "pts": ts.points,
            "xg_favor": f.fmt_num(ts.xg_for), "xg_contra": f.fmt_num(ts.xg_against),
            "actualizado": f.fmt_fecha(ts.last_updated),
        }))
    return out


def stats_equipo(
    session: Session, equipo_id: int, ultimos: int = 5, ahora: datetime | None = None,
) -> dict[str, Any]:
    equipo = _equipo(session, equipo_id)
    if equipo is None:
        return _no_existe("equipo", equipo_id)
    ultimos = f.acotar(ultimos, 1, 20)
    ahora = ahora or datetime.now(timezone.utc)
    partidos = partidos_jugados(session, equipo_id, desde=season_start(ahora))
    recientes = partidos[:ultimos]
    return {
        "equipo": f.sanear(equipo.name),
        "equipo_id": equipo.id,
        "clasificacion": _temporada_equipo(session, equipo_id),
        "medias_por_partido": {
            "temporada": medias_de(partidos),
            "casa": medias_de([p for p in partidos if p.en_casa]),
            "fuera": medias_de([p for p in partidos if not p.en_casa]),
            f"ultimos_{len(recientes)}": medias_de(recientes),
        },
        "balance_temporada": balance(partidos),
        "forma": f.forma_texto([result_letter(p.goles_favor, p.goles_contra) for p in recientes]),
        "ultimos_partidos": [linea_partido(p) for p in recientes],
        "notas": "Temporada desde el 1 de julio, todas las competiciones con datos. «en_contra» = "
                 "lo que hacen sus rivales. Forma: V/E/D, del más reciente al más antiguo.",
    }


def enfrentamientos_directos(
    session: Session, equipo_id: int, rival_id: int, limite: int = 10,
) -> dict[str, Any]:
    a, b = _equipo(session, equipo_id), _equipo(session, rival_id)
    if a is None:
        return _no_existe("equipo", equipo_id)
    if b is None:
        return _no_existe("equipo", rival_id)
    limite = f.acotar(limite, 1, 20)
    h2h = partidos_jugados(session, equipo_id, rival_id=rival_id, limite=limite)
    forma_a = partidos_jugados(session, equipo_id, limite=5)
    forma_b = partidos_jugados(session, rival_id, limite=5)
    return {
        "equipo": f.sanear(a.name),
        "rival": f.sanear(b.name),
        "enfrentamientos": [
            {**linea_partido(p), "liga": f.sanear(p.liga)} for p in h2h
        ],
        "balance_equipo": balance(h2h),
        "medias_en_estos_partidos": medias_de(h2h) if h2h else None,
        "forma_equipo": f.forma_texto([result_letter(p.goles_favor, p.goles_contra) for p in forma_a]),
        "forma_rival": f.forma_texto([result_letter(p.goles_favor, p.goles_contra) for p in forma_b]),
        "notas": "Solo partidos que están en la BD propia (normalmente desde la temporada "
                 "pasada); si no hay enfrentamientos, dilo así.",
    }


# ---------------------------------------------------------------------------
# Jugador
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilaJugador:
    """Una aparición de un jugador reducida a lo que se agrega."""

    match_id: int
    fecha: datetime
    rival: str
    titular: bool
    minutos: int | None
    goles: int | None = None
    asistencias: int | None = None
    tiros: int | None = None
    tiros_puerta: int | None = None
    xg: float | None = None
    paradas: int | None = None
    amarillas: int | None = None
    rojas: int | None = None


CAMPOS_SUMA = ("goles", "asistencias", "tiros", "tiros_puerta", "paradas", "amarillas", "rojas")


def agregar_jugador(filas: list[FilaJugador]) -> dict[str, Any]:
    """Totales, media por partido y por 90' (esta solo con 90+ minutos jugados)."""
    minutos = sum(r.minutos or 0 for r in filas)
    pj = len(filas)
    totales: dict[str, Any] = {c: sum(getattr(r, c) or 0 for r in filas) for c in CAMPOS_SUMA}
    xg_vals = [r.xg for r in filas if r.xg is not None]
    if xg_vals:
        totales["xg"] = f.fmt_num(sum(xg_vals))
    por_partido = {c: f.fmt_num(totales[c] / pj) for c in CAMPOS_SUMA} if pj else {}
    por90 = (
        {c: f.fmt_num(totales[c] * 90 / minutos) for c in CAMPOS_SUMA} if minutos >= 90 else None
    )
    con_tiro_puerta = sum(1 for r in filas if (r.tiros_puerta or 0) >= 1)
    return {
        "partidos": pj,
        "titular": sum(1 for r in filas if r.titular),
        "minutos": minutos,
        "minutos_por_partido": f.fmt_num(minutos / pj, 0) if pj else None,
        "totales": totales,
        "por_partido": por_partido,
        "por_90": por90,
        "partidos_con_1_o_mas_tiros_puerta": f"{con_tiro_puerta}/{pj}" if pj else None,
    }


def _filas_jugador(session: Session, jugador_id: int, desde: datetime) -> list[FilaJugador]:
    filas = session.execute(
        select(PlayerMatchStats, Match)
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .where(PlayerMatchStats.player_id == jugador_id)
        .where(Match.commence_time >= desde)
        .options(joinedload(Match.home_team), joinedload(Match.away_team))
        .order_by(Match.commence_time.desc())
    ).all()
    out = []
    for pms, m in filas:
        rival = m.away_team if pms.is_home else m.home_team
        out.append(FilaJugador(
            match_id=m.id, fecha=m.commence_time, rival=rival.name,
            titular=bool(pms.is_starter), minutos=pms.minutes_played,
            goles=pms.goals, asistencias=pms.assists, tiros=pms.shots,
            tiros_puerta=pms.shots_on_target,
            xg=float(pms.expected_goals) if pms.expected_goals is not None else None,
            paradas=pms.saves, amarillas=pms.yellow_cards, rojas=pms.red_cards,
        ))
    return out


def stats_jugador(
    session: Session, jugador_id: int, ultimos: int = 10, ahora: datetime | None = None,
) -> dict[str, Any]:
    jugador = session.scalar(
        select(Player).where(Player.id == jugador_id).options(joinedload(Player.team))
    )
    if jugador is None:
        return _no_existe("jugador", jugador_id)
    ultimos = f.acotar(ultimos, 1, 20)
    ahora = ahora or datetime.now(timezone.utc)
    filas = _filas_jugador(session, jugador_id, season_start(ahora))
    es_portero = (jugador.position or "").upper().startswith("G")
    recientes = filas[:ultimos]

    def linea(r: FilaJugador) -> dict[str, Any]:
        d = {
            "fecha": f.fmt_fecha(r.fecha), "rival": f.sanear(r.rival),
            "min": r.minutos, "tit": r.titular, "g": r.goles, "a": r.asistencias,
            "tiros": r.tiros, "puerta": r.tiros_puerta,
            "paradas": r.paradas if es_portero else None,
            "amarilla": r.amarillas or None, "roja": r.rojas or None,
        }
        return f.sin_vacios(d)

    return {
        "jugador": f.sanear(jugador.name),
        "jugador_id": jugador.id,
        "equipo": f.sanear(jugador.team.name) if jugador.team else None,
        "posicion": jugador.position,
        "temporada": agregar_jugador(filas),
        f"ultimos_{len(recientes)}": agregar_jugador(recientes),
        "partidos": [linea(r) for r in recientes],
        "notas": "Temporada desde el 1 de julio (todas las competiciones con stats de Flashscore). "
                 "Por 90' solo con 90+ minutos. «puerta» = tiros a puerta.",
    }


# ---------------------------------------------------------------------------
# Partido jugado
# ---------------------------------------------------------------------------

#: Campos de ``MatchStats`` que se devuelven (clave de respuesta, atributo).
CAMPOS_PARTIDO = (
    ("xg", "xg"), ("posesion_pct", "possession_pct"), ("tiros", "shots_total"),
    ("tiros_puerta", "shots_on_target"), ("tiros_fuera", "shots_off_target"),
    ("ocasiones_claras", "big_chances"), ("corners", "corners"),
    ("corners_1t", "corners_1h"), ("corners_2t", "corners_2h"),
    ("goles_1t", "goals_1h"), ("goles_2t", "goals_2h"),
    ("faltas", "fouls"), ("amarillas", "yellow_cards"), ("rojas", "red_cards"),
    ("amarillas_1t", "yellow_cards_1h"), ("amarillas_2t", "yellow_cards_2h"),
    ("fueras_de_juego", "offsides"), ("paradas", "saves"), ("pases_pct", "passes_pct"),
)


def stats_de_lado(st: MatchStats | None) -> dict[str, Any] | None:
    if st is None:
        return None
    out = {}
    for clave, attr in CAMPOS_PARTIDO:
        v = getattr(st, attr)
        out[clave] = f.fmt_num(v) if clave == "xg" else v
    return f.sin_vacios(out)


def stats_partido(session: Session, match_id: int) -> dict[str, Any]:
    m = session.scalar(
        select(Match).where(Match.id == match_id).options(
            joinedload(Match.league), joinedload(Match.home_team), joinedload(Match.away_team),
        )
    )
    if m is None:
        return _no_existe("partido", match_id)
    por_lado = {
        st.is_home: st
        for st in session.scalars(select(MatchStats).where(MatchStats.match_id == m.id)).all()
    }
    jugadores = session.execute(
        select(PlayerMatchStats, Player.name)
        .join(Player, Player.id == PlayerMatchStats.player_id)
        .where(PlayerMatchStats.match_id == m.id)
    ).all()
    destacados = []
    for pms, nombre in jugadores:
        d = f.sin_vacios({
            "jugador": f.sanear(nombre), "lado": "local" if pms.is_home else "visitante",
            "min": pms.minutes_played, "g": pms.goals or None, "a": pms.assists or None,
            "tiros": pms.shots or None, "puerta": pms.shots_on_target or None,
            "paradas": pms.saves or None, "amarilla": pms.yellow_cards or None,
            "roja": pms.red_cards or None,
        })
        if any(k in d for k in ("g", "a", "puerta", "paradas", "amarilla", "roja")):
            destacados.append(d)
    destacados.sort(key=lambda d: (-(d.get("g") or 0), -(d.get("puerta") or 0), d["jugador"]))
    destacados, truncado = f.recortar(destacados, 25)
    out: dict[str, Any] = {
        "match_id": m.id,
        "partido": f.texto_partido(m.home_team.name, m.away_team.name),
        "liga": f.sanear(m.league.name),
        "fecha": f.fmt_fecha(m.commence_time),
        "estado": m.status,
        "marcador": f"{m.home_score}-{m.away_score}" if m.home_score is not None else None,
        "local": stats_de_lado(por_lado.get(True)),
        "visitante": stats_de_lado(por_lado.get(False)),
        "jugadores_destacados": destacados,
    }
    if not por_lado:
        out["aviso"] = "Este partido no tiene stats de equipo en la BD (aún no se ha jugado o faltan)."
    if truncado:
        out["truncado"] = True
    return f.sin_vacios(out)


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------


def elegir_ligas(texto: str, ligas: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Ligas ``(sport_key, nombre)`` que encajan con ``texto`` (sport_key o parte del nombre)."""
    t = normalize_team_name(texto)
    exactas = [lg for lg in ligas if texto.strip().lower() == lg[0] or t == normalize_team_name(lg[1])]
    if exactas:
        return exactas
    return [lg for lg in ligas if t and (t in normalize_team_name(lg[1]) or t in lg[0].replace("_", " "))]


def ordenar_clasificacion(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Por puntos, diferencia de goles y goles a favor; añade la posición."""
    def clave(r: dict[str, Any]) -> tuple:
        gf, gc = r.get("gf") or 0, r.get("gc") or 0
        return (-(r.get("pts") or 0), -(gf - gc), -gf, r.get("equipo") or "")

    ordenadas = sorted(filas, key=clave)
    return [{"pos": i, **r} for i, r in enumerate(ordenadas, start=1)]


def clasificacion(session: Session, liga: str) -> dict[str, Any]:
    ligas = [(sk, nombre) for sk, nombre in session.execute(select(League.sport_key, League.name)).all()]
    elegidas = elegir_ligas(liga, ligas)
    if len(elegidas) != 1:
        return {
            "buscado": f.sanear(liga),
            "candidatos": [{"liga": n, "sport_key": sk} for sk, n in (elegidas or ligas)],
            "aviso": "Indica una sola liga (mejor por su sport_key)." if elegidas
                     else "No encuentro esa liga; estas son las que hay.",
        }
    sport_key, nombre = elegidas[0]
    season = session.scalar(
        select(Season).join(League, League.id == Season.league_id)
        .where(League.sport_key == sport_key)
        .where(Season.id.in_(select(TeamSeasonStats.season_id)))
        .order_by(Season.year_start.desc()).limit(1)
    )
    if season is None:
        return {"liga": nombre, "aviso": "No hay clasificación guardada para esta liga."}
    filas = session.execute(
        select(TeamSeasonStats, Team.name)
        .join(Team, Team.id == TeamSeasonStats.team_id)
        .where(TeamSeasonStats.season_id == season.id)
    ).all()
    tabla = ordenar_clasificacion([
        f.sin_vacios({
            "equipo": f.sanear(nombre_eq), "equipo_id": ts.team_id, "pj": ts.matches_played,
            "v": ts.wins, "e": ts.draws, "d": ts.losses, "gf": ts.goals_for,
            "gc": ts.goals_against, "pts": ts.points,
        })
        for ts, nombre_eq in filas
    ])
    actualizada = max((ts.last_updated for ts, _ in filas), default=None)
    return {
        "liga": f.sanear(nombre), "sport_key": sport_key, "temporada": season.name,
        "actualizada": f.fmt_fecha(actualizada), "tabla": tabla,
    }


# ---------------------------------------------------------------------------
# Bajas
# ---------------------------------------------------------------------------

ESTADO_BAJA = {"missing": "baja", "doubtful": "duda"}


def bajas(
    session: Session, match_id: int | None = None, equipo_id: int | None = None,
    ahora: datetime | None = None,
) -> dict[str, Any]:
    """Bajas y dudas de un partido, o del próximo partido de un equipo."""
    if match_id is None and equipo_id is None:
        return {"error": "Indica match_id o equipo_id."}
    ahora = ahora or datetime.now(timezone.utc)
    stmt = select(Match).options(
        joinedload(Match.league), joinedload(Match.home_team), joinedload(Match.away_team),
    )
    if match_id is not None:
        m = session.scalar(stmt.where(Match.id == match_id))
    else:
        m = session.scalar(
            stmt.where(or_(Match.home_team_id == equipo_id, Match.away_team_id == equipo_id))
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(Match.commence_time >= ahora)
            .order_by(Match.commence_time).limit(1)
        )
    if m is None:
        return {"error": "No encuentro ese partido (o el equipo no tiene partidos próximos)."}
    filas = session.scalars(
        select(MatchAbsence).where(MatchAbsence.match_id == m.id)
        .order_by(MatchAbsence.status.desc(), MatchAbsence.player_name)
    ).all()

    def lado(team_id: int) -> list[dict[str, Any]]:
        return [
            f.sin_vacios({
                "jugador": f.sanear(a.player_name), "estado": ESTADO_BAJA.get(a.status, a.status),
                "motivo": f.sanear(a.reason),
            })
            for a in filas if a.team_id == team_id
        ]

    out: dict[str, Any] = {
        "match_id": m.id,
        "partido": f.texto_partido(m.home_team.name, m.away_team.name),
        "fecha": f.fmt_fecha(m.commence_time),
        "consultado": f.fmt_fecha(m.absences_fetched_at),
        "local": lado(m.home_team_id),
        "visitante": lado(m.away_team_id),
    }
    if m.absences_fetched_at is None:
        out["aviso"] = "Las bajas de este partido aún no se han consultado en Flashscore."
    return f.sin_vacios(out)


__all__ = [
    "FilaJugador",
    "agregar_jugador",
    "bajas",
    "balance",
    "buscar_equipo",
    "buscar_jugador",
    "clasificacion",
    "elegir_ligas",
    "enfrentamientos_directos",
    "medias_compactas",
    "mejores_candidatos",
    "ordenar_clasificacion",
    "puntuar_equipo",
    "puntuar_jugador",
    "stats_de_lado",
    "stats_equipo",
    "stats_jugador",
    "stats_partido",
]
