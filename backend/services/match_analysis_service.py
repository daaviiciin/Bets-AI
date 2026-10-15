"""Servicio de análisis predictivo de un partido.

Combina:

* **Stats de temporada** de ambos equipos (``TeamSeasonStats``).
* **Modelo Poisson** sobre las medias de la liga.
* **Cuotas reales** disponibles en BD para ese partido (mejor cuota por
  selección).
* **Top jugadores ofensivos** de cada equipo (los 5 mejores por
  ``shots_on_target_per_90``).

Devuelve un objeto ``MatchAnalysis`` con todos los datos necesarios para
poblar la página de detalle de partido en el frontend, incluyendo el EV de
cada selección de mercado contra la mejor cuota disponible.

NOTA: este servicio es ortogonal al ``ValueDetector``:

* ``ValueDetector`` calibra fair_probability con el bookie afilado (Pinnacle).
* ``MatchAnalysisService`` calibra fair_probability con el modelo estadístico.

Es saludable que ambas estimaciones converjan; cuando difieren mucho,
probablemente hay información que una de las dos fuentes no está viendo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import (
    CalibrationReport,
    Match,
    Odds,
    Player,
    PlayerSeasonStats,
    Season,
    Team,
    TeamSeasonStats,
)
from backend.services import poisson_model as pm

LOG = logging.getLogger(__name__)

TOP_PLAYERS_LIMIT = 5
"""Número de jugadores top por equipo a incluir en el análisis."""

PROBABILITY_FLOOR = 1e-6


# ---------------------------------------------------------------------------
# Output dataclasses (los schemas Pydantic de respuesta los hace api/matches.py)
# ---------------------------------------------------------------------------


@dataclass
class TeamSnapshot:
    id: int
    name: str
    matches_played: int | None
    wins: int | None
    draws: int | None
    losses: int | None
    goals_for: int | None
    goals_against: int | None
    points: int | None
    goals_for_per_match: float | None
    goals_against_per_match: float | None


@dataclass
class TopPlayerStat:
    name: str
    position: str | None
    matches_played: int | None
    goals: int | None
    shots: int | None
    shots_on_target: int | None
    shots_on_target_per_90: float | None
    xg: float | None


@dataclass
class MarketPrediction:
    """Predicción de un selección concreta dentro de un mercado.

    Si ``best_odds`` es None, no había cuota disponible en BD para esa
    selección y el EV no se puede calcular.
    """

    market: str            # "h2h" / "totals_2.5" / "btts"
    selection: str         # "home" / "draw" / "away" / "over" / "under" / "yes" / "no"
    selection_label: str   # legible: "Real Madrid (gana)" / "Más de 2.5 goles"
    fair_probability: float
    best_odds: float | None
    bookmaker_key: str | None
    implied_probability: float | None
    expected_value: float | None  # decimal (0.149 = +14.9%)
    fair_odds: float


@dataclass
class MatchAnalysis:
    match_id: int
    league_name: str
    commence_time: datetime
    home: TeamSnapshot
    away: TeamSnapshot
    expected_goals_home: float
    expected_goals_away: float
    league_avg_goals: float
    home_top_players: list[TopPlayerStat] = field(default_factory=list)
    away_top_players: list[TopPlayerStat] = field(default_factory=list)
    predictions: list[MarketPrediction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MatchAnalysisService:
    """Genera análisis completo para un partido concreto."""

    def __init__(self, session: Session) -> None:
        self.session = session
        # Cache: el rho que aplica el último CalibrationReport guardado
        # (si lo hay). Lo lee perezosamente al primer analyze().
        self._cached_rho: float | None = None

    # -- main entrypoint ---------------------------------------------------

    def analyze(self, match_id: int) -> MatchAnalysis | None:
        match = self._load_match(match_id)
        if match is None:
            return None

        season = self._latest_season_for_league(match.league_id)
        if season is None:
            LOG.warning("No hay Season para league=%s", match.league_id)
            return self._fallback_no_stats(match, "Sin season de stats para esta liga.")

        home_stats = self._team_stats_for(match.home_team_id, season.id)
        away_stats = self._team_stats_for(match.away_team_id, season.id)
        league_team_stats = self._all_team_stats_for_season(season.id)

        if not (home_stats and away_stats and league_team_stats):
            return self._fallback_no_stats(
                match, "Falta TeamSeasonStats para alguno de los equipos.",
            )

        avgs_home = _to_averages(home_stats)
        avgs_away = _to_averages(away_stats)
        avgs_league = [_to_averages(t) for t in league_team_stats]

        rho = self._latest_dixon_coles_rho()
        expectation = pm.predict(
            avgs_home, avgs_away, avgs_league,
            dixon_coles_rho=rho,
        )
        league_avg = pm.league_average_goals(avgs_league)

        odds_by_market = self._best_odds_by_market(match.id)

        predictions = self._build_predictions(
            expectation,
            odds_by_market,
            home_team_name=match.home_team.name,
            away_team_name=match.away_team.name,
        )

        return MatchAnalysis(
            match_id=match.id,
            league_name=match.league.name,
            commence_time=match.commence_time,
            home=_snapshot(match.home_team, home_stats),
            away=_snapshot(match.away_team, away_stats),
            expected_goals_home=expectation.lambda_home,
            expected_goals_away=expectation.lambda_away,
            league_avg_goals=league_avg,
            home_top_players=self._top_players(match.home_team_id, season.id),
            away_top_players=self._top_players(match.away_team_id, season.id),
            predictions=predictions,
        )

    # -- DB helpers --------------------------------------------------------

    def _load_match(self, match_id: int) -> Match | None:
        return self.session.scalar(
            select(Match)
            .options(
                joinedload(Match.league),
                joinedload(Match.home_team),
                joinedload(Match.away_team),
            )
            .where(Match.id == match_id)
        )

    def _latest_season_for_league(self, league_id: int) -> Season | None:
        return self.session.scalar(
            select(Season)
            .where(Season.league_id == league_id)
            .order_by(Season.year_start.desc())
            .limit(1)
        )

    def _latest_dixon_coles_rho(self) -> float:
        """ρ del último CalibrationReport guardado, o 0.0 si no hay.

        Permite que el modelo en producción aplique automáticamente la
        corrección DC calibrada sin tener que tocar código cada vez que se
        re-calibra. Cacheado por instancia de servicio.
        """
        if self._cached_rho is not None:
            return self._cached_rho
        row = self.session.scalar(
            select(CalibrationReport)
            .order_by(CalibrationReport.created_at.desc())
            .limit(1)
        )
        rho = float(row.dixon_coles_rho) if row and row.dixon_coles_rho is not None else 0.0
        self._cached_rho = rho
        return rho

    def _team_stats_for(self, team_id: int, season_id: int) -> TeamSeasonStats | None:
        return self.session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team_id,
                TeamSeasonStats.season_id == season_id,
            )
        )

    def _all_team_stats_for_season(self, season_id: int) -> list[TeamSeasonStats]:
        return list(self.session.scalars(
            select(TeamSeasonStats).where(TeamSeasonStats.season_id == season_id)
        ))

    def _top_players(self, team_id: int, season_id: int) -> list[TopPlayerStat]:
        rows = self.session.execute(
            select(
                Player.name, Player.position,
                PlayerSeasonStats.matches_played,
                PlayerSeasonStats.goals,
                PlayerSeasonStats.shots,
                PlayerSeasonStats.shots_on_target,
                PlayerSeasonStats.shots_on_target_per_90,
                PlayerSeasonStats.xg,
            )
            .join(Player, Player.id == PlayerSeasonStats.player_id)
            .where(PlayerSeasonStats.team_id == team_id)
            .where(PlayerSeasonStats.season_id == season_id)
            .where(PlayerSeasonStats.shots_on_target_per_90.is_not(None))
            .order_by(PlayerSeasonStats.shots_on_target_per_90.desc())
            .limit(TOP_PLAYERS_LIMIT)
        ).all()
        return [
            TopPlayerStat(
                name=r.name,
                position=r.position,
                matches_played=r.matches_played,
                goals=r.goals,
                shots=r.shots,
                shots_on_target=r.shots_on_target,
                shots_on_target_per_90=float(r.shots_on_target_per_90) if r.shots_on_target_per_90 else None,
                xg=float(r.xg) if r.xg else None,
            )
            for r in rows
        ]

    def _best_odds_by_market(
        self, match_id: int,
    ) -> dict[tuple[str, Optional[Decimal], str], tuple[Decimal, str]]:
        """Mejor cuota disponible para cada (market, point, outcome).

        Si hay varios bookmakers ofreciendo el mismo (h2h Real Madrid),
        devolvemos la cuota MÁS ALTA (la que más beneficio nos da).
        """
        rows = self.session.execute(
            select(Odds)
            .options(joinedload(Odds.bookmaker), joinedload(Odds.market))
            .where(Odds.match_id == match_id)
        ).scalars().all()

        best: dict[tuple[str, Optional[Decimal], str], tuple[Decimal, str]] = {}
        for o in rows:
            if o.bookmaker.key in {"betfair_ex_eu"}:
                continue
            key = (o.market.key, o.outcome_point, o.outcome_name)
            current = best.get(key)
            if current is None or o.price > current[0]:
                best[key] = (o.price, o.bookmaker.key)
        return best

    # -- prediction assembly ----------------------------------------------

    def _build_predictions(
        self,
        expectation: pm.MatchExpectation,
        odds_by_market: dict,
        *,
        home_team_name: str,
        away_team_name: str,
    ) -> list[MarketPrediction]:
        """Construye una lista de selecciones con su EV."""
        out: list[MarketPrediction] = []

        # 1. H2H (1X2)
        h2h_map = [
            ("home", expectation.p_home_win, home_team_name),
            ("draw", expectation.p_draw, "Empate"),
            ("away", expectation.p_away_win, away_team_name),
        ]
        for code, p_fair, label in h2h_map:
            outcome_name = home_team_name if code == "home" else (
                "Draw" if code == "draw" else away_team_name
            )
            best = self._lookup_h2h(odds_by_market, outcome_name)
            out.append(self._make_prediction(
                "h2h", code, label, p_fair, best,
            ))

        # 2. Totales 2.5
        for code, p_fair, label in [
            ("over", expectation.p_over_25, "Más de 2.5 goles"),
            ("under", expectation.p_under_25, "Menos de 2.5 goles"),
        ]:
            outcome_name = "Over" if code == "over" else "Under"
            best = self._lookup_totals(odds_by_market, outcome_name, Decimal("2.5"))
            out.append(self._make_prediction(
                "totals_2.5", code, label, p_fair, best,
            ))

        # 3. Totales 1.5 y 3.5 (sólo si hay cuotas en BD)
        for point, p_over, p_under in [
            (Decimal("1.5"), expectation.p_over_15, expectation.p_under_15),
            (Decimal("3.5"), expectation.p_over_35, expectation.p_under_35),
        ]:
            for code, p_fair, label_prefix in [
                ("over", p_over, "Más de"),
                ("under", p_under, "Menos de"),
            ]:
                outcome_name = "Over" if code == "over" else "Under"
                best = self._lookup_totals(odds_by_market, outcome_name, point)
                if best is None:
                    continue
                label = f"{label_prefix} {point} goles"
                out.append(self._make_prediction(
                    f"totals_{point}", code, label, p_fair, best,
                ))

        # 4. BTTS — no scrapeamos las cuotas reales del Odds API (requiere
        # endpoint per-event, 1 crédito por partido). Si en el futuro las
        # ingestamos, la lookup por nombre las recogerá automáticamente.
        for code, p_fair, label, outcome_name in [
            ("yes", expectation.p_btts_yes, "Ambos marcan: Sí", "Yes"),
            ("no", expectation.p_btts_no, "Ambos marcan: No", "No"),
        ]:
            best = self._lookup_simple(odds_by_market, "btts", outcome_name)
            out.append(self._make_prediction("btts", code, label, p_fair, best))

        # 5. Double Chance (1X / 12 / X2) — mismo caso que BTTS.
        for code, p_fair, label, outcome_name in [
            ("home_or_draw", expectation.p_home_or_draw,
             f"{home_team_name} o empate (1X)", f"{home_team_name} or Draw"),
            ("home_or_away", expectation.p_home_or_away,
             f"{home_team_name} o {away_team_name} (12 · sin empate)",
             f"{home_team_name} or {away_team_name}"),
            ("draw_or_away", expectation.p_draw_or_away,
             f"Empate o {away_team_name} (X2)", f"Draw or {away_team_name}"),
        ]:
            best = self._lookup_simple(odds_by_market, "double_chance", outcome_name)
            out.append(self._make_prediction(
                "double_chance", code, label, p_fair, best,
            ))

        return out

    def _make_prediction(
        self,
        market: str,
        selection: str,
        label: str,
        p_fair: float,
        best_odds: tuple[Decimal, str] | None,
    ) -> MarketPrediction:
        p_fair = max(p_fair, PROBABILITY_FLOOR)
        fair_odds_value = 1.0 / p_fair

        odds_val = float(best_odds[0]) if best_odds else None
        bookmaker = best_odds[1] if best_odds else None
        implied = (1.0 / odds_val) if odds_val else None
        ev = (p_fair * odds_val - 1.0) if odds_val else None

        return MarketPrediction(
            market=market,
            selection=selection,
            selection_label=label,
            fair_probability=p_fair,
            best_odds=odds_val,
            bookmaker_key=bookmaker,
            implied_probability=implied,
            expected_value=ev,
            fair_odds=fair_odds_value,
        )

    def _lookup_h2h(self, odds_by_market: dict, outcome_name: str) -> tuple[Decimal, str] | None:
        return odds_by_market.get(("h2h", None, outcome_name))

    def _lookup_totals(
        self, odds_by_market: dict, outcome_name: str, point: Decimal,
    ) -> tuple[Decimal, str] | None:
        # Las claves en BD son ("totals", Decimal, "Over"/"Under").
        for key, val in odds_by_market.items():
            mk, pt, name = key
            if mk == "totals" and name == outcome_name and pt is not None and pt == point:
                return val
        return None

    def _lookup_simple(
        self, odds_by_market: dict, market_key: str, outcome_name: str,
    ) -> tuple[Decimal, str] | None:
        """Lookup case-insensitive para mercados sin punto (btts, double_chance)."""
        target = outcome_name.lower()
        for key, val in odds_by_market.items():
            mk, pt, name = key
            if mk != market_key or pt is not None:
                continue
            if name.lower() == target:
                return val
        return None

    # -- fallback ----------------------------------------------------------

    def _fallback_no_stats(self, match: Match, note: str) -> MatchAnalysis:
        """Devuelve un análisis vacío cuando no hay stats para el partido."""
        def empty_team(team) -> TeamSnapshot:
            return TeamSnapshot(
                id=team.id, name=team.name,
                matches_played=None, wins=None, draws=None, losses=None,
                goals_for=None, goals_against=None, points=None,
                goals_for_per_match=None, goals_against_per_match=None,
            )
        return MatchAnalysis(
            match_id=match.id,
            league_name=match.league.name,
            commence_time=match.commence_time,
            home=empty_team(match.home_team),
            away=empty_team(match.away_team),
            expected_goals_home=0.0,
            expected_goals_away=0.0,
            league_avg_goals=0.0,
            notes=[note],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_averages(stats: TeamSeasonStats) -> pm.TeamLeagueAverages:
    mp = stats.matches_played or 0
    if mp == 0:
        return pm.TeamLeagueAverages(matches_played=0, goals_for_per_match=0.0, goals_against_per_match=0.0)
    return pm.TeamLeagueAverages(
        matches_played=mp,
        goals_for_per_match=(stats.goals_for or 0) / mp,
        goals_against_per_match=(stats.goals_against or 0) / mp,
    )


def _snapshot(team: Team, stats: TeamSeasonStats) -> TeamSnapshot:
    mp = stats.matches_played or 0
    return TeamSnapshot(
        id=team.id,
        name=team.name,
        matches_played=stats.matches_played,
        wins=stats.wins,
        draws=stats.draws,
        losses=stats.losses,
        goals_for=stats.goals_for,
        goals_against=stats.goals_against,
        points=stats.points,
        goals_for_per_match=(stats.goals_for / mp) if (mp and stats.goals_for is not None) else None,
        goals_against_per_match=(stats.goals_against / mp) if (mp and stats.goals_against is not None) else None,
    )
