"""Evaluación de cuotas / combinadas introducidas manualmente.

Combina:

* **Probabilidad implícita** del libro (sin quitar vig) a partir de la cuota decimal.
* **Probabilidad modelo** cuando existe ``match_id`` y estadísticas de temporada:

  - Fútbol: mismas estimaciones que :class:`~backend.services.match_analysis_service.MatchAnalysisService`
    (Poisson + Dixon–Coles).
  - Baloncesto (``sport_key`` contiene ``basketball``): modelo gaussiano con
    ``TeamSeasonStats.bb_points_*`` (o legado ``goals_*``) y ``League.bb_sd_*``.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.app.schemas.analysis import BetLegIn
from backend.models import Match
from backend.services import basketball_model as bbm
from backend.services.match_analysis_service import MatchAnalysisService


def implied_probability_decimal(decimal_odds: Decimal) -> float:
    o = float(decimal_odds)
    return 1.0 / o if o > 0 else 0.0


def _prediction_key(market: str, selection: str) -> tuple[str, str]:
    return (market.lower().strip(), selection.lower().strip())


def _user_market_to_prediction_key(
    market: str, selection: str, point: Decimal | None,
) -> tuple[str, str] | None:
    m = market.lower().strip()
    sel = selection.lower().strip()
    if m == "h2h":
        return ("h2h", sel)
    if m == "totals":
        if point is None:
            return None
        # Coincide con claves tipo totals_2.5 en MatchAnalysisService
        s = format(float(point)).rstrip("0").rstrip(".")
        return (f"totals_{s}", sel)
    if m == "btts":
        return ("btts", sel)
    if m == "double_chance":
        return ("double_chance", sel)
    return None


def _is_basketball_league(sport_key: str) -> bool:
    return "basketball" in (sport_key or "").lower()


def _basketball_leg_probability(
    proj: bbm.BasketballProjection,
    market: str,
    selection: str,
    point: Decimal | None,
    _home_name: str,
    _away_name: str,
) -> tuple[float | None, str | None]:
    """Devuelve (p_modelo, nota_si_none)."""
    m = market.lower().strip()
    sel = selection.lower().strip()

    if m == "h2h":
        if sel == "home":
            return float(bbm.p_home_win(proj)), None
        if sel == "away":
            ph = float(bbm.p_home_win(proj))
            return 1.0 - ph, None
        if sel == "draw":
            return None, "Empates raros en baloncesto; el modelo no cubre empate."

    if m == "totals" and point is not None:
        line = float(point)
        po = float(bbm.p_game_total_over(proj, line))
        if sel == "over":
            return po, None
        if sel == "under":
            return 1.0 - po, None

    if m == "team_totals_home" and point is not None:
        line = float(point)
        po = float(bbm.p_team_total_over(proj, side="home", line=line))
        if sel == "over":
            return po, None
        if sel == "under":
            return 1.0 - po, None

    if m == "team_totals_away" and point is not None:
        line = float(point)
        po = float(bbm.p_team_total_over(proj, side="away", line=line))
        if sel == "over":
            return po, None
        if sel == "under":
            return 1.0 - po, None

    note = (
        "Mercado no soportado. Usa h2h, totals (partido), "
        "team_totals_home / team_totals_away con outcome_point."
    )
    return None, note


def assess_custom_bet(
    session: Session,
    legs: list[BetLegIn],
    *,
    match_id: int | None,
    assume_independent: bool,
) -> dict:
    """Construye dict alineado con :class:`~backend.app.schemas.analysis.CustomBetAssessmentResponse`."""
    notes: list[str] = []

    svc = MatchAnalysisService(session)
    soccer_index: dict[tuple[str, str], float] = {}
    proj: bbm.BasketballProjection | None = None
    basketball_home_name = basketball_away_name = ""
    match_label: str | None = None
    league_sk: str | None = None

    if match_id is not None:
        match = session.scalar(
            select(Match)
            .options(
                joinedload(Match.league),
                joinedload(Match.home_team),
                joinedload(Match.away_team),
            )
            .where(Match.id == match_id)
        )
        if match is None:
            notes.append(f"Partido id={match_id} no encontrado; solo probabilidades implícitas.")
        else:
            league_sk = match.league.sport_key
            basketball_home_name = match.home_team.name
            basketball_away_name = match.away_team.name
            match_label = f"{match.home_team.name} vs {match.away_team.name}"

            if _is_basketball_league(league_sk):
                season = svc._latest_season_for_league(match.league_id)  # noqa: SLF001
                if season:
                    hs = svc._team_stats_for(match.home_team_id, season.id)  # noqa: SLF001
                    aws = svc._team_stats_for(match.away_team_id, season.id)  # noqa: SLF001
                    if hs and aws:
                        proj = bbm.project_match(hs, aws, match.league)
                        if proj is not None:
                            notes.append(
                                "Baloncesto: modelo gaussiano con bb_points_* (o legado goals_*) "
                                "y sigmas League.bb_sd_*."
                            )
                        else:
                            notes.append("Baloncesto: faltan medias de puntos en TeamSeasonStats.")
                    else:
                        notes.append("Baloncesto: faltan TeamSeasonStats; sin modelo para este partido.")
                else:
                    notes.append("Sin temporada de stats para esta liga.")
            else:
                analysis = svc.analyze(match_id)
                if analysis is None:
                    notes.append("No se pudo cargar análisis del partido.")
                else:
                    if analysis.notes:
                        notes.extend(analysis.notes)
                    for p in analysis.predictions:
                        soccer_index[_prediction_key(p.market, p.selection)] = p.fair_probability

    leg_out: list[dict] = []
    prod_implied = 1.0
    prod_decimal = Decimal("1")

    for leg in legs:
        impl = implied_probability_decimal(leg.decimal_odds)
        prod_implied *= impl
        prod_decimal *= leg.decimal_odds

        p_model: float | None = None
        m_note: str | None = None
        m_ev: float | None = None

        if leg.market and leg.selection:
            pk = _user_market_to_prediction_key(leg.market, leg.selection, leg.outcome_point)
            if pk and pk in soccer_index:
                p_model = soccer_index[pk]
            elif proj is not None:
                bh = _basketball_leg_probability(
                    proj,
                    leg.market,
                    leg.selection,
                    leg.outcome_point,
                    basketball_home_name,
                    basketball_away_name,
                )
                p_model, m_note = bh
            elif match_id is not None:
                if not _is_basketball_league(league_sk or "") and pk:
                    m_note = "No coincide con mercados modelo (comprueba mercado/selección/línea)."
                elif _is_basketball_league(league_sk or "") and proj is None:
                    m_note = "Sin proyección baloncesto (stats incompletas o liga mal configurada)."
        elif match_id is not None:
            m_note = "Añade market + selection (+ línea si aplica) para comparar con el modelo."

        if p_model is not None:
            m_ev = p_model * float(leg.decimal_odds) - 1.0

        leg_out.append(
            {
                "label": leg.label,
                "decimal_odds": str(leg.decimal_odds),
                "implied_probability": impl,
                "model_probability": p_model,
                "model_ev": m_ev,
                "model_note": m_note,
            }
        )

    prod_model_combined: float | None = None
    model_combined_ev = None
    if assume_independent:
        prod_model_combined = 1.0
        for row in leg_out:
            p_m = row["model_probability"]
            if p_m is None:
                prod_model_combined = None
                break
            prod_model_combined *= p_m
        if prod_model_combined is not None:
            model_combined_ev = prod_model_combined * float(prod_decimal) - 1.0
    else:
        notes.append(
            "Probabilidad modelo combinada desactivada (assume_independent=false); "
            "revisa cada pata por separado."
        )

    indep_warn = None
    if assume_independent and len(legs) > 1:
        indep_warn = (
            "Asumir independencia entre patas suele sobrestimar la probabilidad real "
            "de la combinada (correlación en el mismo partido)."
        )

    sources = (
        "Cuotas introducidas por ti (ej. desde Winamax). "
        "Estadísticas modelo: temporada en BD "
        "(ingesta tipo Flashscore / agregados). "
        "Referencia deporte: campo league.sport_key del partido."
    )

    return {
        "match_id": match_id,
        "match_label": match_label,
        "league_sport_key": league_sk,
        "combined_decimal_odds": str(prod_decimal.quantize(Decimal("0.01"))),
        "combined_implied_probability": prod_implied,
        "combined_model_probability": prod_model_combined,
        "model_combined_ev": model_combined_ev,
        "assume_independence": assume_independent,
        "independence_warning": indep_warn,
        "data_sources_note": sources,
        "legs": leg_out,
        "notes": notes,
    }
