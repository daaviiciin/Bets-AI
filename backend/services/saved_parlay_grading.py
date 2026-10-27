"""Corrección automática de patas (misma lógica que el frontend `grade-saved-bet`).

Función pura: recibe la pata, el partido y, si hacen falta, las stats del partido
(:class:`StatsPartido`); los cargadores de BD (``saved_parlays.refresh_one_parlay`` y
``featured_daily_picks._refresh_one_row``) las obtienen con :func:`cargar_stats_partidos`.

Estados de una pata:

- ``pending``: el partido no ha terminado.
- ``pending_stats``: terminado, pero aún no están las stats que pide el mercado (tiros a puerta,
  goles por mitad, stats de jugador). A las :data:`HORAS_ESPERA_STATS` h del saque pasa a
  ``needs_review``.
- ``hit`` / ``miss``: liquidada.
- ``void``: nula (partido aplazado o cancelado).
- ``needs_review``: no se puede liquidar sola (mercado no soportado, empate en la línea, jugador
  no encontrado o que no jugó, datos incoherentes…).

**Limitación conocida (prórroga):** la BD no guarda si un partido fue a prórroga o penaltis
(``Match`` solo tiene ``status`` y marcador; el parser de Flashscore solo mira ``AB=3``). Todos
los mercados liquidados aquí son a 90'. De momento solo se siguen ligas domésticas y la fase de
liga de UEFA, que no tienen prórroga. Como red de seguridad, si hay goles por mitad de los dos
equipos y no suman el marcador final (señal de prórroga o de datos malos), la pata va a
``needs_review``. Las eliminatorias UEFA (febrero en adelante) habrá que excluirlas o guardar
el marcador a 90' antes de que lleguen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from backend.models.match import (
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_FINISHED,
    MATCH_STATUS_POSTPONED,
    Match,
)
from backend.services.match_results_updater import _names_match, _normalize as _norm_team
from backend.services.team_name_matching import player_names_match
from backend.services.winamax_normalization import (
    canonical_market_key,
    highest_scoring_half_side,
    normalize_btts_outcome,
    normalize_double_chance_outcome,
    normalize_h2h_outcome,
    normalize_totals_outcome,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

LegResult = Literal["pending", "pending_stats", "hit", "miss", "void", "needs_review"]

#: Horas desde el saque tras las que una pata sin stats deja de esperar y pasa a revisión.
HORAS_ESPERA_STATS = 72

_ESTADOS_NULOS = frozenset({MATCH_STATUS_POSTPONED, MATCH_STATUS_CANCELED})

# Totales de goles a 90' que se liquidan solo con el marcador.
_GOLES_MARCADOR = frozenset({"totals", "team_home_totals_goals", "team_away_totals_goals"})

# Totales por mitad: clave → (mitad, lado) con lado None = partido.
_GOLES_MITAD: dict[str, tuple[str, str | None]] = {
    "match_first_half_totals": ("1h", None),
    "team_home_first_half_totals_goals": ("1h", "home"),
    "team_away_first_half_totals_goals": ("1h", "away"),
    "match_second_half_totals": ("2h", None),
    "team_home_second_half_totals_goals": ("2h", "home"),
    "team_away_second_half_totals_goals": ("2h", "away"),
}

# Tiros a puerta: clave → lado (None = partido).
_TIROS_PUERTA: dict[str, str | None] = {
    "match_sot_totals": None,
    "team_home_sot_totals": "home",
    "team_away_sot_totals": "away",
}

_TIROS_PUERTA_JUGADOR = frozenset({"player_sot", "player_sot_alt"})

_RE_OVER = re.compile(r"\b(over|m[áa]s)\b", re.I)
_RE_UNDER = re.compile(r"\b(under|menos)\b", re.I)


@dataclass(frozen=True)
class StatsEquipo:
    """Stats de un equipo en el partido que usa la liquidación (de ``match_stats``)."""

    goals_1h: int | None = None
    goals_2h: int | None = None
    shots_on_target: int | None = None


@dataclass(frozen=True)
class StatsJugador:
    """Stats de un jugador en el partido (de ``player_match_stats``)."""

    nombre: str
    shots_on_target: int | None = None
    minutes_played: int | None = None


@dataclass(frozen=True)
class StatsPartido:
    """Stats disponibles de un partido. ``None`` en un campo = aún no cargadas."""

    local: StatsEquipo | None = None
    visitante: StatsEquipo | None = None
    #: ``None`` = stats de jugador sin consultar; tupla vacía = consultadas y sin filas.
    jugadores: tuple[StatsJugador, ...] | None = None

    def lado(self, side: str) -> StatsEquipo | None:
        return self.local if side == "home" else self.visitante


def _norm(s: str) -> str:
    return s.strip().lower().replace(".", "")


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _over_under(
    actual: int, line: float | None, side: str,
) -> Literal["hit", "miss", "needs_review"]:
    """Liquida un más/menos. Empate exacto con la línea (línea entera) → revisión."""
    if line is None or side not in {"over", "under"} or actual == line:
        return "needs_review"
    hit = actual > line if side == "over" else actual < line
    return "hit" if hit else "miss"


def _prorroga_sospechosa(hs: int, aw: int, stats: StatsPartido | None) -> bool:
    """Goles por mitad de los dos equipos que no suman el marcador (¿prórroga?)."""
    if stats is None or stats.local is None or stats.visitante is None:
        return False
    for st, score in ((stats.local, hs), (stats.visitante, aw)):
        if st.goals_1h is None or st.goals_2h is None:
            return False
        if st.goals_1h + st.goals_2h != score:
            return True
    return False


def _doble_oportunidad(
    outcome: str, *, home: str, away: str, hs: int, aw: int,
) -> Literal["hit", "miss", "needs_review"]:
    """``1X`` / ``X2`` / ``12`` o ``"<local> or Draw"`` (forma guardada en BD) → resultado."""
    canon = normalize_double_chance_outcome(outcome, home_team=home, away_team=away)
    parts = [p.strip() for p in canon.split(" or ")]
    if len(parts) != 2:
        return "needs_review"
    lados: set[str] = set()
    for p in parts:
        if p.lower() in {"draw", "empate", "x"}:
            lados.add("D")
        elif _names_match(_norm_team(p), _norm_team(home)):
            lados.add("H")
        elif _names_match(_norm_team(p), _norm_team(away)):
            lados.add("A")
        else:
            return "needs_review"
    if len(lados) != 2:
        return "needs_review"
    real = "H" if hs > aw else ("A" if aw > hs else "D")
    return "hit" if real in lados else "miss"


def _goles_mitad(stats: StatsPartido | None, mitad: str, lados: Iterable[str]) -> int | None:
    """Goles de ``mitad`` (``1h``/``2h``) sumados en ``lados``; ``None`` si falta algún dato."""
    total = 0
    for ld in lados:
        st = stats.lado(ld) if stats else None
        v = getattr(st, f"goals_{mitad}", None) if st else None
        if v is None:
            return None
        total += v
    return total


def _jugador_unico(nombre: str, jugadores: Iterable[StatsJugador]) -> StatsJugador | None:
    """Jugador del partido cuyo nombre encaja, si es único (misma regla que el ingester)."""
    js = list(jugadores)
    exact = [j for j in js if j.nombre == nombre]
    if len(exact) == 1:
        return exact[0]
    cand = [j for j in js if player_names_match(nombre, j.nombre)]
    return cand[0] if len(cand) == 1 else None


def _lado_de_outcome(outcome_name: str) -> str:
    if _RE_UNDER.search(outcome_name):
        return "under"
    if _RE_OVER.search(outcome_name):
        return "over"
    return ""


def grade_single_leg(
    leg: dict[str, Any],
    match: Match | None,
    stats: StatsPartido | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Devuelve ``result``, ``score_text`` y datos mínimos de la pata.

    ``stats`` solo hace falta para mercados que no se liquidan con el marcador (mitades,
    tiros a puerta, props de jugador); sin ellas esas patas quedan en ``pending_stats``
    hasta :data:`HORAS_ESPERA_STATS` h después del saque (``now`` inyectable para tests).
    """
    mid = leg.get("match_id")
    home_name = (
        match.home_team.name if match and match.home_team else str(leg.get("home_team", ""))
    )
    away_name = (
        match.away_team.name if match and match.away_team else str(leg.get("away_team", ""))
    )

    if match is not None and match.status in _ESTADOS_NULOS:
        return {"result": "void", "score_text": None, "match_id": mid}

    hs = match.home_score if match else None
    aw = match.away_score if match else None

    if (
        match is None
        or match.status != MATCH_STATUS_FINISHED
        or hs is None
        or aw is None
    ):
        return {"result": "pending", "score_text": None, "match_id": mid}

    score_text = f"{home_name} {hs} · {away_name} {aw}"

    def out(result: str) -> dict[str, Any]:
        return {"result": result, "score_text": score_text, "match_id": mid}

    if _prorroga_sospechosa(hs, aw, stats):
        return out("needs_review")

    def sin_stats() -> dict[str, Any]:
        ahora = now or datetime.now(timezone.utc)
        limite = _aware(match.commence_time) + timedelta(hours=HORAS_ESPERA_STATS)
        return out("needs_review" if ahora >= limite else "pending_stats")

    raw_mk = str(leg.get("market_key", "")).strip()
    nk = _norm(canonical_market_key(raw_mk) if raw_mk else raw_mk)
    outcome = str(leg.get("outcome_name", ""))

    if nk in _TIROS_PUERTA_JUGADOR:
        if stats is None or stats.jugadores is None:
            return sin_stats()
        nombre = str(leg.get("player_name") or "").strip()
        jugador = _jugador_unico(nombre, stats.jugadores) if nombre else None
        # Sin jugador claro, o que no jugó (las casas suelen anular): a revisar.
        if jugador is None or jugador.shots_on_target is None or jugador.minutes_played == 0:
            return out("needs_review")
        side = _lado_de_outcome(outcome)
        return out(_over_under(jugador.shots_on_target, _to_float(leg.get("outcome_point")), side))

    if nk.startswith("player_"):
        return out("needs_review")

    if nk == "double_chance":
        return out(_doble_oportunidad(outcome, home=home_name, away=away_name, hs=hs, aw=aw))

    if nk == "h2h":
        picked = normalize_h2h_outcome(outcome, home_team=home_name, away_team=away_name)
        if picked == "Draw":
            return out("hit" if hs == aw else "miss")
        if _names_match(_norm_team(picked), _norm_team(home_name)):
            return out("hit" if hs > aw else "miss")
        if _names_match(_norm_team(picked), _norm_team(away_name)):
            return out("hit" if aw > hs else "miss")
        return out("needs_review")

    if nk == "btts":
        on = _norm(normalize_btts_outcome(outcome))
        both = hs >= 1 and aw >= 1
        wants_yes = on in {"yes", "sí", "si"}
        wants_no = on == "no"
        if not wants_yes and not wants_no:
            return out("needs_review")
        return out("hit" if (both if wants_yes else not both) else "miss")

    line = _to_float(leg.get("outcome_point"))
    side = _norm(normalize_totals_outcome(outcome))

    if nk in _GOLES_MARCADOR:
        actual = hs if nk == "team_home_totals_goals" else aw if nk == "team_away_totals_goals" else hs + aw
        return out(_over_under(actual, line, side))

    if nk in _GOLES_MITAD:
        mitad, lado = _GOLES_MITAD[nk]
        goles = _goles_mitad(stats, mitad, ("home", "away") if lado is None else (lado,))
        if goles is None:
            return sin_stats()
        return out(_over_under(goles, line, side))

    if nk == "match_highest_scoring_half":
        elegida = highest_scoring_half_side(outcome)
        if elegida is None:
            return out("needs_review")
        g1 = _goles_mitad(stats, "1h", ("home", "away"))
        g2 = _goles_mitad(stats, "2h", ("home", "away"))
        if g1 is None or g2 is None:
            return sin_stats()
        real = "1h" if g1 > g2 else "2h" if g2 > g1 else "tie"
        return out("hit" if real == elegida else "miss")

    if nk in _TIROS_PUERTA:
        lado = _TIROS_PUERTA[nk]
        lados = ("home", "away") if lado is None else (lado,)
        valores: list[int] = []
        for ld in lados:
            st = stats.lado(ld) if stats else None
            if st is None or st.shots_on_target is None:
                return sin_stats()
            valores.append(st.shots_on_target)
        return out(_over_under(sum(valores), line, side))

    return out("needs_review")


def overall_from_leg_results(results: list[str]) -> LegResult | str:
    """Resultado de la apuesta a partir de sus patas.

    Orden: un fallo manda; luego lo que falta por jugar (``pending``), lo que espera stats
    (``pending_stats``) y lo que hay que revisar. Si todas son nulas, la apuesta es nula.
    **Nulas mezcladas con aciertos → ``needs_review``**: la casa recalcula la cuota sin la pata
    nula, pero el beneficio se calcula con la cuota guardada; para no dar un beneficio falso se
    deja a revisar (en ``grading_snapshot`` va la ``cuota_efectiva`` para corregirla a mano).
    """
    if any(r == "miss" for r in results):
        return "miss"
    if any(r == "pending" for r in results):
        return "pending"
    if any(r == "pending_stats" for r in results):
        return "pending_stats"
    if any(r == "needs_review" for r in results):
        return "needs_review"
    if results and all(r == "void" for r in results):
        return "void"
    if any(r == "void" for r in results):
        return "needs_review"
    return "hit"


def cuota_efectiva(legs: list[dict[str, Any]]) -> Decimal | None:
    """Producto de las cuotas de las patas no nulas (``None`` si no hay ninguna nula)."""
    if not any(lg.get("result") == "void" for lg in legs):
        return None
    prod = Decimal("1")
    for lg in legs:
        if lg.get("result") != "void":
            prod *= Decimal(str(lg["offered_odds"]))
    return prod.quantize(Decimal("0.0001"))


def stats_partido_desde_filas(
    filas_equipo: Iterable[Any],
    filas_jugador: Iterable[tuple[str, Any]] | None,
) -> StatsPartido:
    """Construye :class:`StatsPartido` a partir de filas ORM (``MatchStats`` y
    ``(nombre, PlayerMatchStats)``). ``filas_jugador=None`` = stats de jugador sin consultar."""
    local = visitante = None
    for st in filas_equipo:
        se = StatsEquipo(
            goals_1h=st.goals_1h, goals_2h=st.goals_2h, shots_on_target=st.shots_on_target,
        )
        if st.is_home:
            local = se
        else:
            visitante = se
    jugadores = None
    if filas_jugador is not None:
        jugadores = tuple(
            StatsJugador(
                nombre=nombre,
                shots_on_target=pms.shots_on_target,
                minutes_played=pms.minutes_played,
            )
            for nombre, pms in filas_jugador
        )
    return StatsPartido(local=local, visitante=visitante, jugadores=jugadores)


def cargar_stats_partidos(session: Session, matches: Iterable[Match]) -> dict[int, StatsPartido]:
    """Stats de equipo y de jugador de los partidos **terminados** (una consulta de cada)."""
    from sqlalchemy import select

    from backend.models import MatchStats, Player, PlayerMatchStats

    terminados = [m for m in matches if m.status == MATCH_STATUS_FINISHED]
    if not terminados:
        return {}
    ids = [m.id for m in terminados]
    por_equipo: dict[int, list[Any]] = {i: [] for i in ids}
    for st in session.scalars(select(MatchStats).where(MatchStats.match_id.in_(ids))).all():
        por_equipo[st.match_id].append(st)
    por_jugador: dict[int, list[tuple[str, Any]]] = {i: [] for i in ids}
    filas = session.execute(
        select(Player.name, PlayerMatchStats)
        .join(Player, Player.id == PlayerMatchStats.player_id)
        .where(PlayerMatchStats.match_id.in_(ids)),
    ).all()
    for nombre, pms in filas:
        por_jugador[pms.match_id].append((nombre, pms))
    out: dict[int, StatsPartido] = {}
    for m in terminados:
        consultado = m.player_stats_fetched_at is not None or bool(por_jugador[m.id])
        out[m.id] = stats_partido_desde_filas(
            por_equipo[m.id], por_jugador[m.id] if consultado else None,
        )
    return out


__all__ = [
    "HORAS_ESPERA_STATS",
    "LegResult",
    "StatsEquipo",
    "StatsJugador",
    "StatsPartido",
    "cargar_stats_partidos",
    "cuota_efectiva",
    "grade_single_leg",
    "overall_from_leg_results",
    "stats_partido_desde_filas",
]
