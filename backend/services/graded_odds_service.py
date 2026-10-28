"""Construye vistas de mercado exhaustivas para el dashboard (todas las cuotas)."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session, joinedload

from backend.models import Bookmaker, League, Match, Odds, Season, Snapshot, TeamSeasonStats
from backend.services import market_probabilities as mp
from backend.services import poisson_model as pm
from backend.services import value_calculator as vc
from backend.services.value_detector import ValueDetector
from backend.services.winamax_odds_ingester import WINAMAX_TOP5_FOOTBALL_KEYS

WINAMAX_SOURCE = "winamax_es"


def _finite_decimal_or_none(value: Decimal | None) -> Decimal | None:
    """Pydantic rechaza Decimal no finitos (`finite_number`); sin esto FastAPI puede devolver 500 al serializar."""
    if value is None:
        return None
    try:
        return value if value.is_finite() else None
    except (ArithmeticError, AttributeError, ValueError):
        return None


def _implied_probability(odds: Decimal) -> Decimal | None:
    if odds <= Decimal("1"):
        return None
    return (Decimal("1") / odds).quantize(Decimal("0.0001"))


def _model_pricing_snapshot(
    expectation: pm.MatchExpectation | None,
    offered_odds: Decimal,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Meta numérica para la API (simulador / columnas), no para el texto de resumen."""
    if expectation is None:
        return None, None, _implied_probability(offered_odds)
    lh = Decimal(str(round(expectation.lambda_home, 3)))
    la = Decimal(str(round(expectation.lambda_away, 3)))
    return lh, la, _implied_probability(offered_odds)


def _norm_outcome(outcome_name: str) -> str:
    return (outcome_name or "").strip().lower()


def _format_decimal_point(pt: Decimal | None) -> str | None:
    if pt is None:
        return None
    s = format(pt, "f").rstrip("0").rstrip(".")
    return s if s else "0"


def _eq_ci(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _totals_is_over_under(on: str) -> str | None:
    """Inferencia tolerante (Over / Under tras normalización o español residual)."""
    s = _norm_outcome(on)
    if not s:
        return None
    if s in {"over", "under"}:
        return s
    if "under" in s or "menos" in s or s.startswith("-"):
        return "under"
    if "over" in s or "más" in s or "mas " in (s + " ") or "+" in s:
        return "over"
    return None


def _double_chance_opening_sentence(outcome_name: str, hn: str, an: str) -> str:
    """Una línea inicial alineada con la combinación apostada."""
    nm = (outcome_name or "").strip().lower()
    hl, al = hn.lower(), an.lower()
    has_home = hl in nm
    has_away = al in nm
    has_draw = "draw" in nm or "empate" in nm
    nm_tok = nm.replace("-", "").replace(" ", "")
    if nm_tok == "1x" or (has_home and has_draw and not has_away):
        return (
            f"Doble oportunidad 1× ({hn} no pierde): valen victoria local y empate."
        )
    if nm_tok == "x2" or (has_away and has_draw and not has_home):
        return (
            f"Doble oportunidad ×2 ({an} no pierde): valen empate y victoria visitante."
        )
    if nm_tok == "12" or (has_home and has_away and not has_draw):
        return (
            "Doble oportunidad 12: valen las dos victorias; normalmente no cuenta el empate (comprueba siempre la condición del ticket)."
        )
    return (
        f"Doble oportunidad entre {hn} y {an}: amplías opciones respecto al 1×2 simple."
    )


_MATCH_STAT_KIND_ES = {
    "corners": "los córners que suma la casa entre los dos equipos",
    "cards": "las tarjetas que cuenta la casa en el partido",
    "shots": "los tiros totales entre ambos, según publique la casa",
    "sot": "los tiros a puerta entre ambos",
    "first_half": "los goles que entran solo en la primera parte",
    "first_half_corners": "los córners que entran solo en la primera parte",
    "first_half_cards": "las tarjetas que entran solo en la primera parte",
}


def _per_match_goals(st: TeamSeasonStats | None) -> tuple[float | None, float | None]:
    """Medias temporada (todos los partidos ligados al registro standings)."""
    if st is None:
        return None, None
    mp = st.matches_played or 0
    if mp <= 0:
        return None, None
    gf = float(st.goals_for or 0) / mp
    ga = float(st.goals_against or 0) / mp
    return gf, ga


def _season_stats_pair(session: Session, match: Match) -> tuple[TeamSeasonStats | None, TeamSeasonStats | None]:
    season_id = session.scalar(
        select(Season.id)
        .where(Season.league_id == match.league_id)
        .order_by(Season.year_start.desc())
        .limit(1)
    )
    if season_id is None:
        return None, None
    home_st = session.scalar(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == match.home_team_id,
            TeamSeasonStats.season_id == season_id,
        )
    )
    away_st = session.scalar(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == match.away_team_id,
            TeamSeasonStats.season_id == season_id,
        )
    )
    return home_st, away_st


def _no_stats_fallback() -> tuple[str, ...]:
    return (
        "No hay estadísticas de temporada cargadas para estos equipos. "
        "Sincroniza la liga en la herramienta de datos para ver números aquí.",
    )


def _summary_lines_for_bet(
    *,
    match: Match,
    home_st: TeamSeasonStats | None,
    away_st: TeamSeasonStats | None,
    market_key: str,
    outcome_name: str,
    outcome_point: Decimal | None,
    player_name: str | None,
) -> tuple[str, ...]:
    """Resumen orientado al mercado de la línea (lo que debe leer quien cobra ese ticket)."""
    hn = match.home_team.name
    an = match.away_team.name
    mk_raw = market_key or ""
    mk = mk_raw.lower().strip()
    on = _norm_outcome(outcome_name)
    lines: list[str] = []

    hgf, hga = _per_match_goals(home_st)
    agf, aga = _per_match_goals(away_st)
    hs_ok = home_st is not None and home_st.matches_played
    ash_ok = away_st is not None and away_st.matches_played

    def _combined_attack_note() -> str | None:
        if hgf is None or agf is None:
            return None
        return (
            f"En liga, entre los dos suman de media ~{hgf + agf:.2f} goles anotados por partido esta temporada."
        )

    # --- Totales GOLES PARTIDO ---
    if mk == "totals":
        pts = _format_decimal_point(outcome_point)
        ou = _totals_is_over_under(outcome_name or "")
        if pts is None:
            lines.append(
                f"Más/menos goles en el partido completo ({hn} vs {an}). Falta la línea numérica en datos."
            )
            if hgf is not None and agf is not None:
                n = _combined_attack_note()
                if n:
                    lines.append(n)
            return tuple(lines) if lines else _no_stats_fallback()
        if ou is not None:
            dire = "Más de" if ou == "over" else "Menos de"
            lines.append(
                f"{dire} {pts} goles en el partido: cuenta todo lo marcado entre los dos en tiempo reglamentario."
            )
            if hgf is not None and hga is not None:
                lines.append(
                    f"{hn}: ~{hgf:.2f} a favor y ~{hga:.2f} encajados por partido en liga.",
                )
            if agf is not None and aga is not None:
                lines.append(
                    f"{an}: ~{agf:.2f} a favor y ~{aga:.2f} encajados por partido en liga.",
                )
            note = _combined_attack_note()
            if note:
                lines.append(note)
            if ou == "over":
                lines.append("Las líneas a más encajan cuando ambos equipos crean ocasiones; casa/fuera y bajas siguen importando.")  # noqa: E501
            else:
                lines.append(
                    "Las líneas a menos encajan cuando el partido suele ser cerrado o con pocas ocasiones claras.",  # noqa: E501
                )
            return tuple(lines)
        lines.append(f"Línea de goles totales ({pts}) entre {hn} y {an}: las medias de liga sirven solo como contexto.")
        if hgf is not None and agf is not None and (n := _combined_attack_note()):
            lines.append(n)
        return tuple(lines) if lines else _no_stats_fallback()

    # --- Goles equipo local / visitante ---
    if mk == "team_home_totals_goals":
        pts = _format_decimal_point(outcome_point)
        label = (
            f"Solo cuentan los goles que marca el local ({hn}). La casa suele publicar una línea tipo {pts}."  # noqa: E501
            if pts
            else f"Solo cuentan los goles del local ({hn}), no el marcador conjunto ni los goles del rival."  # noqa: E501
        )
        lines.append(label)
        if hs_ok and home_st is not None:
            hs = home_st
            mp = hs.matches_played or 0
            if hgf is not None and hs.goals_for is not None and mp > 0:
                lines.append(
                    f"{hn}: {hs.goals_for} goles en {mp} partidos de liga (~{hgf:.2f} por partido)."
                )
        if ash_ok and away_st is not None and aga is not None:
            lines.append(
                f"{an} encaja de media ~{aga:.2f} por partido en liga—sirve para ver si hay margen para que marque el local.",  # noqa: E501
            )
        if not hs_ok:
            lines = [label, *_no_stats_fallback()[:1]]
        return tuple(lines)

    if mk == "team_away_totals_goals":
        pts = _format_decimal_point(outcome_point)
        label = (
            f"Solo cuentan los goles del visitante ({an}). Referencia habitual ~{pts} si la lista la muestra."  # noqa: E501
            if pts
            else f"Solo cuentan los goles del visitante ({an}), no los dos equipos sumados."  # noqa: E501
        )
        lines.append(label)
        if ash_ok and away_st is not None:
            aa = away_st
            mp = aa.matches_played or 0
            if agf is not None and aa.goals_for is not None and mp > 0:
                lines.append(
                    f"{an}: {aa.goals_for} goles en {mp} partidos (~{agf:.2f} por partido)."
                )
        if hs_ok and hga is not None:
            lines.append(
                f"{hn} concede de media ~{hga:.2f} por partido—dato habitual para valorar si el visitante puede marcar.",  # noqa: E501
            )
        if not ash_ok:
            lines = list(lines[:1]) + [*list(_no_stats_fallback())[:1]]
        return tuple(lines)

    if mk == "match_first_half_totals":
        pts = _format_decimal_point(outcome_point)
        ou = _totals_is_over_under(outcome_name or "")
        lines.append(
            "Solo cuenta la primera parte (hasta el descanso). El marcador final del partido no decide esta cuota."  # noqa: E501
        )
        if ou is not None and pts:
            lines.append(
                f"Selección: {'más' if ou == 'over' else 'menos'} de {pts} goles entre los dos solo en ese tramo."  # noqa: E501
            )
        note = _combined_attack_note()
        if note:
            lines.append(
                "Las medias que ves son del partido completo; úsalas solo como referencia lejana para la 1ª parte."
            )
            lines.append(note)
        elif not hs_ok and not ash_ok:
            lines.extend(list(_no_stats_fallback()))
        return tuple(lines)

    if mk == "team_home_first_half_totals_goals":
        pts = _format_decimal_point(outcome_point)
        intro = (
            f"Solo goles del local ({hn}) en la primera parte."
            + (f" Línea habitual ~{pts}." if pts else "")
        )
        lines.append(intro)
        if hs_ok and home_st is not None and hgf is not None:
            lines.append(
                f"{hn}: ~{hgf:.2f} goles por partido en liga (dato global; en la 1ª parte suele haber menos).",  # noqa: E501
            )
        if not hs_ok:
            lines = [*lines[:1], *_no_stats_fallback()[:1]]
        return tuple(lines)

    if mk == "team_away_first_half_totals_goals":
        pts = _format_decimal_point(outcome_point)
        intro = (
            f"Solo goles del visitante ({an}) en la primera parte (no ambos equipos juntos)."
            + (f" Línea habitual ~{pts}." if pts else "")
        )
        lines.append(intro)
        if ash_ok and away_st is not None and agf is not None:
            lines.append(
                f"{an}: ~{agf:.2f} goles por partido en liga (dato global; la 1ª parte suele ir más abajo).",  # noqa: E501
            )
        if not ash_ok:
            lines = [*lines[:1], *_no_stats_fallback()[:1]]
        return tuple(lines)

    # --- Ambos marcan ---
    if mk == "btts":
        ol = _norm_outcome(outcome_name or "")
        if not ol:
            yn = True
        else:
            yn = ol in frozenset({"yes", "y", "sí", "si"})
            if ol == "no":
                yn = False
        opener = (
            f"Sí ambos marcan ({hn} y {an}): hace falta gol de cada equipo."
            if yn
            else "No ambos marcan: con que uno se quede a cero suele ganar el no."
        )
        lines.append(opener)
        if hgf is not None:
            lines.append(f"{hn}: ~{hgf:.2f} goles marcados por partido en liga.")
        if hga is not None:
            lines.append(f"{hn} encaja ~{hga:.2f} por partido; si es alto suele ayudar al sí.")
        if agf is not None:
            lines.append(f"{an}: ~{agf:.2f} a favor por partido en liga.")
        if aga is not None:
            lines.append(f"{an} encaja ~{aga:.2f} por partido.")
        if hgf is None and agf is None:
            lines = [*lines[:1], *_no_stats_fallback()[:1]]
        return tuple(lines)

    # --- 1X2 ---
    if mk == "h2h":
        on_raw = outcome_name or ""
        if _eq_ci(on_raw, "Draw") or on in {"draw", "tie"} or "empate" in on:
            lines.append(
                f"1×2 al empate entre {hn} y {an}.",
            )
        elif _eq_ci(on_raw, hn):
            lines.append(f"1×2 a victoria del local ({hn}).")
        elif _eq_ci(on_raw, an):
            lines.append(
                f"1×2 a victoria del visitante ({an})."
            )
        else:
            lines.append(f"1×2 {hn}–{an}: confirma en el ticket si es local, empate o visitante.")
        if hs_ok and home_st is not None:
            hh = home_st
            mp = hh.matches_played or 0
            parts_dc: list[str] = []
            if hh.points is not None:
                parts_dc.append(f"{hn}: {hh.points} puntos en {mp} partidos de liga.")
            if hgf is not None and hh.goals_for is not None:
                parts_dc.append(f"{hh.goals_for} goles a favor (~{hgf:.2f} por partido).")  # noqa: E501
            if hh.goals_against is not None and hga is not None:
                parts_dc.append(f"{hh.goals_against} encajados (~{hga:.2f} por partido).")  # noqa: E501
            if parts_dc:
                lines.append(" ".join(parts_dc))
        if ash_ok and away_st is not None:
            mp_a = away_st.matches_played or 0
            chunks: list[str] = []
            if away_st.points is not None:
                chunks.append(f"{an}: {away_st.points} puntos en {mp_a} partidos.")
            if agf is not None and away_st.goals_for is not None:
                chunks.append(f"{away_st.goals_for} goles a favor (~{agf:.2f} por partido).")  # noqa: E501
            if chunks:
                lines.append(" ".join(chunks))
        return tuple(lines)

    # --- Doble oportunidad ---
    if mk == "double_chance":
        lines.append(_double_chance_opening_sentence(outcome_name or "", hn, an))
        if hs_ok and home_st is not None:
            hh = home_st
            wins = hh.wins
            pts = hh.points if hh.points is not None else None
            if wins is not None and pts is not None:
                lines.append(f"{hn}: {pts} puntos en {hh.matches_played or 0} partidos.")
            elif pts is not None:
                lines.append(f"{hn}: {pts} puntos en tabla.")
        if ash_ok and away_st and away_st.wins is not None:
            lines.append(f"{an}: {away_st.wins} victorias en temporada.")
        return tuple(lines) if lines else _no_stats_fallback()

    # --- Estadísticas de partido (córners, tarjetas, tiros…) ---
    if mk.startswith("match_") and mk.endswith("_totals"):
        inner = mk[len("match_") : -len("_totals")] if mk.startswith("match_") else mk
        hint = _MATCH_STAT_KIND_ES.get(inner, "una estadística concreta del partido según la casa")
        pts = _format_decimal_point(outcome_point)
        ou = _totals_is_over_under(outcome_name or "")
        lines.append(
            f"Mide {hint}. "
            "Aquí no hay promedio de liga para esta estadística: lo que ves abajo son solo goles por partido como contexto muy aproximado."  # noqa: E501
        )
        if ou is not None and pts:
            lines.append(
                f"Selección: {'más' if ou == 'over' else 'menos'} de {pts} según la línea que muestra la casa."
            )
        note = _combined_attack_note()
        if note:
            lines.append(
                note + " Con córners/tarjetas/tiros no siempre hay relación directa con goles."
            )
        elif not hs_ok and not ash_ok:
            lines.extend(list(_no_stats_fallback()))
        return tuple(lines)

    # --- Jugador ---
    if mk.startswith("player_"):
        genre = mk[len("player_") :].replace("_", " ").strip()
        human = genre or "jugador"
        if player_name:
            lines.append(
                f"Mercado de jugador ({human}) sobre {player_name}. El equipo y el partido marcan la lectura."
            )
        else:
            lines.append(
                f"Mercado de jugador ({human}). El papel del jugador depende también del ritmo del equipo."
            )
        lines.append(
            f"Contexto rápido del duelo ({hn} local): los números de liga aquí son solo marco general."
        )
        if hgf is not None and hga is not None:
            lines.append(f"{hn}: ~{hgf:.2f} a favor y ~{hga:.2f} encajados por partido.")
        if agf is not None and aga is not None:
            lines.append(f"{an}: ~{agf:.2f} a favor y ~{aga:.2f} encajados por partido.")
        if player_name:
            lines.append(
                f"Para valorar bien este mercado mira sobre todo la forma reciente de {player_name}."
            )
        return tuple(lines)

    # --- Genérico: misma filosofía tabla pero menos largo ---
    if hs_ok and home_st is not None:
        mp_h = home_st.matches_played or 0
        if home_st.points is not None:
            gf_text = ""
            if home_st.goals_for is not None and home_st.goals_against is not None:
                gf_text = f"{home_st.goals_for} goles a favor y {home_st.goals_against} encajados en {mp_h} partidos."
            lines.append(
                f"{hn}: {home_st.points} puntos en liga. "
                + (gf_text if gf_text else "Sin detalle de goles en datos.")
            )
        else:
            lines.append(_club_season_light(hn, home_st))
    elif home_st is not None:
        ln = _club_season_light(hn, home_st)
        if ln:
            lines.append(ln)

    if ash_ok and away_st is not None:
        mp_a = away_st.matches_played or 1
        if away_st.points is not None:
            gol_text = ""
            if away_st.goals_for is not None and away_st.goals_against is not None:
                gol_text = f"{away_st.goals_for} goles a favor y {away_st.goals_against} encajados en {mp_a} partidos."
            lines.append(
                f"{an}: {away_st.points} puntos en liga. "
                + (gol_text if gol_text else "Sin detalle de goles en datos.")
            )
        else:
            ln2 = _club_season_light(an, away_st)
            if ln2:
                lines.append(ln2)
    elif away_st is not None:
        ln2 = _club_season_light(an, away_st)
        if ln2:
            lines.append(ln2)

    return tuple(lines) if lines else _no_stats_fallback()


def _club_season_light(team_name: str, st: TeamSeasonStats | None) -> str | None:
    if st is None:
        return None
    mp = st.matches_played
    if not mp:
        return None
    gf = st.goals_for
    gc = st.goals_against
    if gf is None or gc is None:
        return f"{team_name}: temporada cargada ({mp} partidos) pero sin goles a favor/contra en datos."
    return (
        f"{team_name}: {gf} marcado(s) y {gc} encajados en temporada ({mp} partidos)."
    )


@dataclass(frozen=True)
class GradedOddsLine:
    match_id: int
    league: str
    league_sport_key: str
    home_team: str
    away_team: str
    commence_time: datetime
    market_key: str
    outcome_name: str
    outcome_point: Decimal | None
    player_name: str | None
    bookmaker_key: str
    bookmaker_title: str
    offered_odds: Decimal
    confidence: str
    model_probability: Decimal | None
    model_ev: Decimal | None
    # ``True`` si la cuota es <= ``MAX_RECOMMENDABLE_ODDS``; si no, la línea es solo informativa.
    recommendable: bool
    lambda_home: Decimal | None = None
    lambda_away: Decimal | None = None
    implied_probability: Decimal | None = None
    model_summary_lines: tuple[str, ...] = ()
    #: Modelo que dio ``model_probability``: la etiqueta de la versión v2 o ``legacy-v1``.
    model_version: str = "legacy-v1"
    #: v2: la P está mezclada con la del mercado (había cuotas de todo el mercado).
    con_mercado: bool = False
    #: v2: partidos del equipo con menos partidos en los datos del modelo.
    partidos_min: int | None = None


def confidence_from_model_ev(model_ev: Decimal | None) -> str:
    """Etiqueta estable para todas las líneas (con o sin modelo)."""
    if model_ev is None:
        return "unknown"
    mv = float(model_ev)
    if mv >= 0.05:
        return "high"
    if mv >= 0.01:
        return "medium"
    if mv >= -0.025:
        return "low"
    return "low"


def grade_line(offered_odds: Decimal, model_ev: Decimal | None) -> tuple[str, bool]:
    """``(confidence, recommendable)`` de una línea del libro.

    Por encima de ``MAX_RECOMMENDABLE_ODDS`` la línea se muestra (con P(modelo) y EV si los
    hay) pero no se califica: ``confidence`` queda en ``unknown`` y ``recommendable`` en
    ``False``, así nunca cuenta como valor.
    """
    if not vc.is_recommendable_odds(offered_odds):
        return "unknown", False
    return confidence_from_model_ev(model_ev), True


def _best_winamax_snapshot_per_match(session: Session, match_ids: set[int]) -> dict[int, int]:
    """Por cada ``match_id``, el snapshot Winamax más reciente que ya tiene cuotas guardadas."""
    if not match_ids:
        return {}
    stmt = (
        select(Odds.match_id, Snapshot.id, Snapshot.captured_at)
        .join(Snapshot, Snapshot.id == Odds.snapshot_id)
        .where(Snapshot.source == WINAMAX_SOURCE)
        .where(Odds.match_id.in_(match_ids))
    )
    best: dict[int, tuple[int, datetime]] = {}
    for mid, sid, cap in session.execute(stmt):
        cur = best.get(mid)
        if cur is None or cap > cur[1] or (cap == cur[1] and sid > cur[0]):
            best[mid] = (sid, cap)
    return {mid: sid for mid, (sid, _) in best.items()}


def _interleave_odds_rows_for_dashboard(
    rows: list[Odds],
    match_by_id: dict[int, Match],
) -> list[Odds]:
    """Reordena filas Odds para que ``max_lines`` no consuma todo el cupo con una sola competición.

    El query original ordenaba por ``match_id``; en BD los ids suelen correlacionar con el orden de
    ingesta (p. ej. muchos partidos de una misma liga seguidos), de modo que el tope cortaba antes
    de llegar a Premier / Serie A / Bundesliga.
    """
    by_sk: dict[str, deque[Odds]] = defaultdict(deque)
    tail: list[Odds] = []
    top_set = frozenset(WINAMAX_TOP5_FOOTBALL_KEYS)
    for o in rows:
        m = o.match if o.match is not None else match_by_id.get(o.match_id)
        if m is None or m.league is None:
            tail.append(o)
            continue
        sk = (m.league.sport_key or "").strip()
        if not sk:
            tail.append(o)
            continue
        by_sk[sk].append(o)
    keys_order = [sk for sk in WINAMAX_TOP5_FOOTBALL_KEYS if sk in by_sk]
    keys_order.extend(sorted(sk for sk in by_sk if sk not in top_set))
    interleaved: list[Odds] = []
    while True:
        progressed = False
        for sk in keys_order:
            dq = by_sk[sk]
            if dq:
                interleaved.append(dq.popleft())
                progressed = True
        if not progressed:
            break
    interleaved.extend(tail)
    return interleaved


def list_graded_winamax_odds(
    session: Session,
    *,
    horizon_hours: int = 48,
    max_lines: int = 3500,
    bookmaker_key: str | None = None,
    sport_key: str | None = None,
    match_id: int | None = None,
    log: logging.Logger | None = None,
) -> list[GradedOddsLine]:
    """Últimas cuotas Winamax por liga sobre partidos próximos, todas calificadas.

    ``match_id`` limita a un partido (página de partido): todas sus líneas, sin el reparto
    entre partidos del dashboard.

    * ``confidence`` y ``model_ev`` salen del modelo donde aplica (misma lógica
      que ``ValueDetector``); el resto queda como ``unknown`` / ``model_ev=null``.
    * Las líneas con cuota > ``MAX_RECOMMENDABLE_ODDS`` salen igual (con P(modelo) y EV),
      pero con ``recommendable=False`` y ``confidence='unknown'`` (ver :func:`grade_line`).
    """
    _log = log or logging.getLogger(__name__)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_hours)

    stmt_m = (
        select(Match)
        .where(Match.commence_time >= now)
        .where(Match.commence_time <= horizon)
        .join(Match.league)
        .where(~League.sport_key.ilike("%basketball%"))
        .options(
            joinedload(Match.league),
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        )
        .order_by(Match.commence_time)
    )
    matches = session.scalars(stmt_m).all()
    if not matches:
        return []
    if sport_key:
        sk_filter = sport_key.strip()
        matches = [m for m in matches if m.league is not None and m.league.sport_key == sk_filter]
        if not matches:
            return []
    if match_id is not None:
        matches = [m for m in matches if m.id == match_id]
        if not matches:
            return []

    match_by_id = {m.id: m for m in matches}
    allowed_match_ids = {m.id for m in matches}

    best_snap = _best_winamax_snapshot_per_match(session, allowed_match_ids)
    if not best_snap:
        _log.info("graded_odds: ningún partido del horizonte tiene cuotas Winamax en BD.")
        return []

    pair_list = list(best_snap.items())
    stmt_o = (
        select(Odds)
        .where(tuple_(Odds.match_id, Odds.snapshot_id).in_(pair_list))
        .where(Odds.price > Decimal("1"))
    )
    if bookmaker_key:
        bm = session.scalar(select(Bookmaker.id).where(Bookmaker.key == bookmaker_key))
        if bm is None:
            return []
        stmt_o = stmt_o.where(Odds.bookmaker_id == bm)

    stmt_o = (
        stmt_o.options(
            joinedload(Odds.bookmaker),
            joinedload(Odds.market),
            joinedload(Odds.player),
            joinedload(Odds.match).joinedload(Match.league),
            joinedload(Odds.match).joinedload(Match.home_team),
            joinedload(Odds.match).joinedload(Match.away_team),
        )
        .order_by(Odds.match_id, Odds.market_id, Odds.id)
    )
    rows = list(session.scalars(stmt_o).all())
    rows = _interleave_odds_rows_for_dashboard(rows, match_by_id)

    det = ValueDetector(
        session,
        min_ev=Decimal("-1"),
        min_ev_model=Decimal("-1"),
        official_pipeline=False,
        log=_log,
    )

    expectation_cache: dict[int, object | None] = {}
    season_stats_pair_cache: dict[int, tuple[TeamSeasonStats | None, TeamSeasonStats | None]] = {}

    # Modelo v2 (si hay versión activa): P calibrada y mezclada con el mercado sin margen.
    from backend.services.model_registry import MIN_PARTIDOS_PREDICCION, modelo_activo

    modelo = modelo_activo(session, now)
    prediccion_cache: dict[int, mp.MatchPrediction | None] = {}
    sin_margen_cache: dict[int, dict[mp.ClaveLinea, float]] = {}
    filas_por_partido: dict[int, list[Odds]] = defaultdict(list)
    for o in rows:
        if o.player_id is None:
            filas_por_partido[o.match_id].append(o)

    def prediccion_v2(m: Match) -> mp.MatchPrediction | None:
        if m.id not in prediccion_cache:
            ok = modelo is not None and modelo.partidos_min(m.home_team_id, m.away_team_id) >= MIN_PARTIDOS_PREDICCION
            prediccion_cache[m.id] = (
                modelo.prediccion(m.league.sport_key or "", m.home_team_id, m.away_team_id)
                if ok and modelo is not None else None
            )
        return prediccion_cache[m.id]

    def sin_margen(m: Match) -> dict[mp.ClaveLinea, float]:
        if m.id not in sin_margen_cache:
            lineas = [
                (o.market.key if o.market else "", o.outcome_name,
                 float(o.outcome_point) if o.outcome_point is not None else None, float(o.price))
                for o in filas_por_partido.get(m.id, [])
            ]
            sin_margen_cache[m.id] = mp.probabilidades_sin_margen(lineas, m.home_team.name, m.away_team.name)
        return sin_margen_cache[m.id]

    def probabilidad_v2(o: Odds, m: Match, mk: str) -> tuple[Decimal, bool] | None:
        """``(P, con_mercado)`` del v2 para la línea, o ``None`` si no la cubre."""
        if modelo is None or o.player_id is not None or mk not in mp.MERCADOS_V2:
            return None
        pred = prediccion_v2(m)
        if pred is None:
            return None
        punto = float(o.outcome_point) if o.outcome_point is not None else None
        p = mp.probability_for_line(
            pred, mk, o.outcome_name, punto, m.home_team.name, m.away_team.name, modelo.calibracion,
        )
        if p is None:
            return None
        p_mercado = sin_margen(m).get((mk, o.outcome_name, punto))
        p = mp.mezclar(p, p_mercado, modelo.peso_mercado)
        return Decimal(str(round(min(max(p, 1e-6), 1 - 1e-6), 6))), p_mercado is not None

    def expectation_for(mid: int):
        if mid not in expectation_cache:
            m = match_by_id.get(mid)
            expectation_cache[mid] = det._expectation_for_match(m) if m else None  # noqa: SLF001
        return expectation_cache[mid]

    def season_pair_for(mid: int) -> tuple[TeamSeasonStats | None, TeamSeasonStats | None]:
        if mid not in season_stats_pair_cache:
            mm = match_by_id.get(mid)
            season_stats_pair_cache[mid] = _season_stats_pair(session, mm) if mm is not None else (None, None)
        return season_stats_pair_cache[mid]

    out: list[GradedOddsLine] = []
    for o in rows:
        m = o.match
        if m is None or m.id not in allowed_match_ids:
            continue
        safe_price = _finite_decimal_or_none(o.price)
        if safe_price is None:
            continue
        exp = expectation_for(m.id)
        exp_match = exp if isinstance(exp, pm.MatchExpectation) else None
        mk = o.market.key if o.market else ""
        pn = o.player.name if o.player else None
        v2 = probabilidad_v2(o, m, mk)
        if v2 is not None:
            model_prob, con_mercado = v2
        else:
            con_mercado = False
            model_prob = det._model_probability(  # noqa: SLF001
                exp,
                m,
                market_key=mk,
                outcome_name=o.outcome_name,
                outcome_point=o.outcome_point,
                home_team_name=m.home_team.name,
                away_team_name=m.away_team.name,
                player_id=o.player_id,
            )
        model_ev: Decimal | None = None
        model_prob_out = _finite_decimal_or_none(model_prob)
        if model_prob_out is not None and model_prob_out > Decimal("0"):
            try:
                model_ev = vc.expected_value(safe_price, model_prob_out)
            except ValueError:
                model_ev = None
        conf, recommendable = grade_line(safe_price, model_ev)
        lh, la, implied = _model_pricing_snapshot(exp_match, safe_price)
        pred = prediccion_v2(m) if v2 is not None else None
        if pred is not None:
            lh = Decimal(str(round(pred.lambda_home, 3)))
            la = Decimal(str(round(pred.lambda_away, 3)))
        hp, asp = season_pair_for(m.id)
        summ = _summary_lines_for_bet(
            match=m,
            home_st=hp,
            away_st=asp,
            market_key=mk,
            outcome_name=o.outcome_name or "",
            outcome_point=o.outcome_point,
            player_name=pn,
        )
        out.append(
            GradedOddsLine(
                match_id=m.id,
                league=m.league.name,
                league_sport_key=m.league.sport_key or "",
                home_team=m.home_team.name,
                away_team=m.away_team.name,
                commence_time=m.commence_time,
                market_key=mk,
                outcome_name=o.outcome_name,
                outcome_point=_finite_decimal_or_none(o.outcome_point) if o.outcome_point is not None else None,
                player_name=pn,
                bookmaker_key=o.bookmaker.key,
                bookmaker_title=o.bookmaker.title,
                offered_odds=safe_price,
                confidence=conf,
                model_probability=model_prob_out,
                model_ev=_finite_decimal_or_none(model_ev),
                recommendable=recommendable,
                lambda_home=_finite_decimal_or_none(lh),
                lambda_away=_finite_decimal_or_none(la),
                implied_probability=_finite_decimal_or_none(implied),
                model_summary_lines=summ,
                model_version=modelo.tag if (v2 is not None and modelo is not None) else "legacy-v1",
                con_mercado=con_mercado,
                partidos_min=(
                    modelo.partidos_min(m.home_team_id, m.away_team_id)
                    if (v2 is not None and modelo is not None) else None
                ),
            )
        )

    return keep_best_lines(out, max_lines)


def keep_best_lines(lines: list[GradedOddsLine], max_lines: int) -> list[GradedOddsLine]:
    """Si hay más de ``max_lines``, se quedan las de mayor EV del modelo (sin EV, al final).

    Antes el tope cortaba por orden de recorrido y podía dejar fuera justo las líneas con
    valor. Primero van las recomendables con EV; las de cuota > ``MAX_RECOMMENDABLE_ODDS``
    con EV van detrás (son informativas, no pueden quitarles sitio). Se conserva el orden
    original de las que quedan.
    """
    if len(lines) <= max_lines:
        return lines
    ranked = sorted(
        range(len(lines)),
        key=lambda i: (
            lines[i].recommendable and lines[i].model_ev is not None,
            lines[i].model_ev is not None,
            lines[i].model_ev or Decimal("0"),
        ),
        reverse=True,
    )
    keep = set(ranked[:max_lines])
    return [line for i, line in enumerate(lines) if i in keep]


