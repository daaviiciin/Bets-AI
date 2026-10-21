"""Detector de value bets escaneando la BD.

Algoritmo (mercados ``h2h`` y ``totals``):

1. Por cada partido próximo, obtener todas las cuotas del mercado.
2. Agrupar por bookmaker, descartando los que tengan el mercado incompleto.
3. Calcular el margen (vig) de cada bookmaker. El de menor margen es el
   **bookie afilado** (referencia de probabilidad real de mercado).
4. Calcular ``fair_probability`` quitando el vig al afilado.
5. Para cada bookmaker no-afilado, calcular el **EV de mercado** contra esa
   ``fair_probability``.
6. (Doble criterio) Si tenemos ``TeamSeasonStats`` para los dos equipos,
   pedir al modelo Poisson la **probabilidad del modelo** del mismo outcome
   y calcular el **EV del modelo** contra la cuota ofrecida. En fútbol se
   mezcla la media de temporada con los **últimos partidos como local /
   visitante** (misma liga) para que los totales y el 1X2 reflejen forma
   reciente, no sólo la tabla global.
7. En **1X2** se exige una **probabilidad mínima del modelo** (configurable)
   para no promocionar victorias visitantes o dogs sólo por EV de mercado.
8. Devolver las apuestas que pasen los filtros, ordenadas por **EV del modelo**
   y luego EV de mercado (para priorizar picks alineados con estadística).

Modos
-----

* ``require_model_agreement=False`` (default): se devuelve toda apuesta con
  ``EV_market >= min_ev``. El campo ``confidence`` indica si el modelo
  acompaña o no.
* ``require_model_agreement=True``: solo se devuelven apuestas en las que el
  modelo Poisson **también** estima EV positivo (``EV_model >= min_ev_model``).
  Para partidos sin stats (no podemos predecir) la apuesta se descarta.

Mercados ignorados
------------------
* ``h2h_lay`` (Betfair Exchange).

Tope de cuota
-------------
Nunca se emite una apuesta con cuota > ``value_calculator.MAX_RECOMMENDABLE_ODDS`` (4,00).
Esas cuotas sí entran en el cálculo del margen del mercado, pero no son candidatas.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.models import (
    Bookmaker,
    League,
    Market,
    Match,
    Odds,
    PlayerSeasonStats,
    Season,
    TeamSeasonStats,
)
from backend.services import basketball_model as bbm
from backend.services import poisson_model as pm
from backend.services import recent_goal_form as rgf
from backend.services import value_calculator as vc

SUPPORTED_MARKETS = {
    "h2h",
    "totals",
    "btts",
    "double_chance",
    "team_home_totals_goals",
    "team_away_totals_goals",
    "player_sot",
    "player_sot_alt",
}
PLAYER_SOT_MARKETS = frozenset({"player_sot", "player_sot_alt"})
PLAYER_SHOT_MARKETS = frozenset({"player_shots", "player_shots_alt"})
PLAYER_GOAL_FREQ_MARKETS = frozenset({"player_anytime_scorer", "player_to_score_2plus"})
PLAYER_CARD_MARKETS = frozenset({"player_card_yes_no"})
IGNORED_BOOKMAKERS = {"betfair_ex_eu"}
DEFAULT_MIN_EV = Decimal("0.0")
DEFAULT_MIN_EV_MODEL = Decimal("0.0")
DEFAULT_HORIZON_HOURS = 24 * 14
# ``implied_probability`` exige cuota estrictamente > 1; datos corruptos no deben tumbar el API.
MIN_ODDS_FOR_IMPLIED = Decimal("1")

# Outcomes 1X2 — el feed de Odds API usa el nombre del equipo para H/A
# y la string ``"Draw"`` para el empate (en inglés siempre).
DRAW_OUTCOMES = {"draw", "tie"}
OVER_OUTCOMES = {"over"}
UNDER_OUTCOMES = {"under"}
BTTS_YES_OUTCOMES = {"yes", "both teams to score: yes"}
BTTS_NO_OUTCOMES = {"no", "both teams to score: no"}

_RE_PLAYER_PROP_TAIL = re.compile(
    r"\s+(over|under)\s+([\d]+[.,]?[\d]*)\s*$",
    re.IGNORECASE,
)


def _infer_yes_no_label(outcome_name: str) -> str | None:
    stripped = outcome_name.strip().lower()
    if stripped in {"sí", "si", "yes", "y"}:
        return "yes"
    if stripped == "no":
        return "no"
    return None


def _parse_player_prop_side(
    outcome_name: str,
    outcome_point: Decimal | None,
) -> str | None:
    raw = outcome_name.strip().replace(",", ".")
    m = _RE_PLAYER_PROP_TAIL.search(raw)
    if not m:
        return None
    side = m.group(1).lower()
    try:
        line_val = float(m.group(2).replace(",", "."))
    except ValueError:
        return None
    if outcome_point is None:
        return side
    if abs(line_val - float(outcome_point)) > 5e-3:
        return None
    return side


@dataclass(frozen=True)
class ValueBet:
    """Una apuesta con valor positivo detectada por el sistema.

    Campos clásicos (criterio de mercado):

    * ``offered_odds`` — cuota del bookmaker no-afilado.
    * ``fair_probability`` — probabilidad real estimada quitando vig al afilado.
    * ``ev`` — EV de mercado: ``offered_odds * fair_probability - 1``.

    Campos del doble criterio (modelo Poisson):

    * ``model_probability`` — probabilidad asignada por el modelo al outcome.
    * ``model_ev`` — EV usando la probabilidad del modelo.
    * ``model_agrees`` — ``True`` si tanto market como model dan EV positivo.
    * ``confidence`` — ``high`` / ``medium`` / ``low`` / ``unknown``.

    El ``model_*`` puede ser ``None`` si no tenemos stats del partido (equipo
    no en BD, o sin ``TeamSeasonStats`` cargadas).
    """

    match_id: int
    league: str
    home_team: str
    away_team: str
    commence_time: datetime
    market_key: str
    outcome_name: str
    outcome_point: Decimal | None
    bookmaker_key: str
    bookmaker_title: str
    offered_odds: Decimal

    # Criterio de mercado (bookie afilado)
    fair_probability: Decimal
    fair_odds: Decimal
    ev: Decimal
    sharp_bookie_key: str
    sharp_bookie_margin: Decimal

    # Criterio del modelo (Poisson)
    model_probability: Decimal | None
    model_fair_odds: Decimal | None
    model_ev: Decimal | None
    model_agrees: bool | None
    confidence: str  # "high" | "medium" | "low" | "unknown"

    player_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["commence_time"] = self.commence_time.isoformat()
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = float(v)
        return d


def _odds_row_price_ok(o: Odds) -> bool:
    return o.price is not None and o.price > MIN_ODDS_FOR_IMPLIED


def _fair_probabilities_safe(odds_by_outcome: dict[str, Decimal]) -> dict[str, Decimal] | None:
    try:
        return vc.fair_probabilities(odds_by_outcome)
    except ValueError:
        return None


def _blend_season_with_venue_rates(
    season: pm.TeamLeagueAverages,
    recent: tuple[float, float, int] | None,
    weight: float,
) -> pm.TeamLeagueAverages:
    """Combina medias de temporada con últimos partidos en un rol (local o visitante)."""
    if recent is None or weight <= 0.0:
        return season
    w = min(1.0, max(0.0, weight))
    gf_r, ga_r, n = recent
    gf = (1.0 - w) * float(season.goals_for_per_match) + w * gf_r
    ga = (1.0 - w) * float(season.goals_against_per_match) + w * ga_r
    return pm.TeamLeagueAverages(
        matches_played=max(season.matches_played, n),
        goals_for_per_match=gf,
        goals_against_per_match=ga,
    )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class ValueDetector:
    """Detecta apuestas con EV positivo en partidos próximos."""

    def __init__(
        self,
        session: Session,
        *,
        min_ev: Decimal = DEFAULT_MIN_EV,
        min_ev_model: Decimal = DEFAULT_MIN_EV_MODEL,
        require_model_agreement: bool = False,
        official_pipeline: bool | None = None,
        bookmaker_allowlist: frozenset[str] | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        self.session = session
        self.min_ev = min_ev
        self.min_ev_model = min_ev_model
        self.require_model_agreement = require_model_agreement
        self.log = log or logging.getLogger(__name__)
        st = get_settings()
        self.official_pipeline = (
            bool(official_pipeline)
            if official_pipeline is not None else bool(st.official_data_pipeline)
        )
        if bookmaker_allowlist is not None:
            self._bookmaker_allowlist = bookmaker_allowlist
        elif self.official_pipeline:
            self._bookmaker_allowlist = frozenset({st.primary_bookmaker_key})
        else:
            self._bookmaker_allowlist = None

        # Caches (se llenan al primer partido analizado de cada liga).
        # league_id -> {team_id: averages}
        self._team_avgs_by_league: dict[int, dict[int, pm.TeamLeagueAverages]] = {}
        # league_id -> list[averages] (todos los equipos de la liga, para μ)
        self._league_avgs: dict[int, list[pm.TeamLeagueAverages]] = {}
        # ρ del último CalibrationReport (lazy-loaded en el primer match).
        self._cached_rho: float | None = None
        self._recent_form_enabled = bool(st.value_bets_use_recent_goal_form)
        self._recent_form_n = int(st.value_bets_recent_form_matches)
        self._recent_form_weight = float(st.value_bets_recent_form_weight)
        self._h2h_min_model_prob = Decimal(str(st.value_bets_h2h_min_model_probability))

    # -- API pública --------------------------------------------------------

    def detect_upcoming(
        self,
        *,
        horizon_hours: int = DEFAULT_HORIZON_HOURS,
        league_sport_key: str | None = None,
    ) -> list[ValueBet]:
        now = datetime.now(timezone.utc)
        until = now + timedelta(hours=horizon_hours)

        stmt = (
            select(Match)
            .where(Match.commence_time >= now)
            .where(Match.commence_time <= until)
            .options(
                joinedload(Match.league),
                joinedload(Match.home_team),
                joinedload(Match.away_team),
            )
            .order_by(Match.commence_time)
        )
        if league_sport_key:
            stmt = stmt.join(Match.league).where(League.sport_key == league_sport_key)

        matches = self.session.scalars(stmt).all()
        self.log.info("Analizando %d partidos próximos", len(matches))

        all_bets: list[ValueBet] = []
        for match in matches:
            all_bets.extend(self.detect_for_match(match))
        all_bets.sort(
            key=lambda vb: (
                float(vb.model_ev) if vb.model_ev is not None else -1e18,
                float(vb.ev),
            ),
            reverse=True,
        )
        return all_bets

    def detect_for_match(self, match: Match) -> list[ValueBet]:
        """Detecta value bets para un solo partido en todos los mercados soportados."""
        expectation = self._expectation_for_match(match)
        bets: list[ValueBet] = []
        bets.extend(self._detect_market(match, "h2h", expectation))
        bets.extend(self._detect_market(match, "totals", expectation))
        bets.extend(self._detect_market(match, "btts", expectation))
        bets.extend(self._detect_market(match, "double_chance", expectation))
        bets.extend(self._detect_market(match, "team_home_totals_goals", expectation))
        bets.extend(self._detect_market(match, "team_away_totals_goals", expectation))
        league_sk = (match.league.sport_key or "").lower()
        if "basketball" not in league_sk:
            bets.extend(self._detect_player_sot_props(match, expectation))
        return bets

    # -- Predicción del modelo ---------------------------------------------

    def _expectation_for_match(self, match: Match) -> pm.MatchExpectation | None:
        """Devuelve la ``MatchExpectation`` del modelo Poisson o ``None`` si no
        hay stats para los dos equipos."""
        avgs_by_team = self._ensure_league_cached(match.league_id)
        if avgs_by_team is None:
            return None
        home_avg = avgs_by_team.get(match.home_team_id)
        away_avg = avgs_by_team.get(match.away_team_id)
        if home_avg is None or away_avg is None:
            return None
        league_sk = (match.league.sport_key or "").lower()
        if self._recent_form_enabled and "basketball" not in league_sk:
            rh = rgf.recent_rates_as_home(
                self.session,
                team_id=match.home_team_id,
                league_id=match.league_id,
                limit=self._recent_form_n,
                exclude_match_id=match.id,
            )
            ra = rgf.recent_rates_as_visitor(
                self.session,
                team_id=match.away_team_id,
                league_id=match.league_id,
                limit=self._recent_form_n,
                exclude_match_id=match.id,
            )
            home_avg = _blend_season_with_venue_rates(home_avg, rh, self._recent_form_weight)
            away_avg = _blend_season_with_venue_rates(away_avg, ra, self._recent_form_weight)
        league_teams = self._league_avgs[match.league_id]
        return pm.predict(
            home_avg, away_avg, league_teams,
            dixon_coles_rho=self._latest_dixon_coles_rho(),
        )

    def _latest_dixon_coles_rho(self) -> float:
        """Lee el ρ del último CalibrationReport o 0.0 si no hay."""
        if self._cached_rho is not None:
            return self._cached_rho
        from backend.models import CalibrationReport  # import local para evitar ciclo
        row = self.session.scalar(
            select(CalibrationReport)
            .order_by(CalibrationReport.created_at.desc())
            .limit(1)
        )
        rho = float(row.dixon_coles_rho) if row and row.dixon_coles_rho is not None else 0.0
        self._cached_rho = rho
        return rho

    def _ensure_league_cached(self, league_id: int) -> dict[int, pm.TeamLeagueAverages] | None:
        """Lazy-load TeamSeasonStats para la temporada más reciente de la liga.

        Devuelve ``None`` si no hay stats cargadas para esa liga (caso típico
        en competiciones europeas tipo Champions, que aún no scrapeamos).
        """
        if league_id in self._team_avgs_by_league:
            cached = self._team_avgs_by_league[league_id]
            return cached or None

        season = self.session.execute(
            select(Season)
            .where(Season.league_id == league_id)
            .order_by(Season.year_start.desc())
        ).scalars().first()
        if season is None:
            self._team_avgs_by_league[league_id] = {}
            return None

        rows = self.session.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.season_id == season.id)
        ).scalars().all()

        avgs_by_team: dict[int, pm.TeamLeagueAverages] = {}
        all_avgs: list[pm.TeamLeagueAverages] = []
        for ts in rows:
            avg = _to_averages(ts)
            if avg is None:
                continue
            avgs_by_team[ts.team_id] = avg
            all_avgs.append(avg)

        self._team_avgs_by_league[league_id] = avgs_by_team
        self._league_avgs[league_id] = all_avgs
        return avgs_by_team or None

    def _basketball_model_probability(
        self,
        match: Match,
        *,
        market_key: str,
        outcome_name: str,
        outcome_point: Decimal | None,
    ) -> Decimal | None:
        season = self.session.scalar(
            select(Season)
            .where(Season.league_id == match.league_id)
            .order_by(Season.year_start.desc())
            .limit(1)
        )
        if season is None:
            return None
        hs = self.session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == match.home_team_id,
                TeamSeasonStats.season_id == season.id,
            )
        )
        aws = self.session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == match.away_team_id,
                TeamSeasonStats.season_id == season.id,
            )
        )
        if hs is None or aws is None:
            return None
        proj = bbm.project_match(hs, aws, match.league)
        if proj is None:
            return None

        if market_key == "h2h":
            name = outcome_name.strip()
            if name.lower() in DRAW_OUTCOMES:
                return None
            if name == match.home_team.name:
                return Decimal(str(bbm.p_home_win(proj)))
            if name == match.away_team.name:
                ph = float(bbm.p_home_win(proj))
                return Decimal(str(max(0.0, min(1.0, 1.0 - ph))))
            return None

        if market_key == "totals" and outcome_point is not None:
            po = float(bbm.p_game_total_over(proj, float(outcome_point)))
            name = outcome_name.strip().lower()
            if name in OVER_OUTCOMES:
                return Decimal(str(po))
            if name in UNDER_OUTCOMES:
                return Decimal(str(1.0 - po))
            return None

        return None

    def _player_expected_sot_for_match(self, match: Match, player_id: int) -> float | None:
        """λ esperados de tiros a puerta (partido) desde stats de temporada del jugador."""
        season = self.session.scalars(
            select(Season)
            .where(Season.league_id == match.league_id)
            .order_by(Season.year_start.desc())
            .limit(1)
        ).first()
        if season is None:
            return None
        stats = self.session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == player_id,
                PlayerSeasonStats.season_id == season.id,
            )
        )
        if stats is None:
            return None

        mp = max(int(stats.matches_played or 0), 1)
        rate_per_90: float | None = None
        if stats.shots_on_target_per_90 is not None and float(stats.shots_on_target_per_90) > 0:
            rate_per_90 = float(stats.shots_on_target_per_90)
        elif stats.minutes is not None and stats.minutes > 0 and stats.shots_on_target is not None:
            rate_per_90 = (stats.shots_on_target / float(stats.minutes)) * 90.0
        elif stats.shots_on_target is not None and stats.starts is not None and stats.starts > 0:
            mins_per_start = float(stats.minutes or 0) / float(stats.starts)
            mins_per_start = max(55.0, min(95.0, mins_per_start or 76.0))
            rate_per_90 = (stats.shots_on_target / mins_per_start) * 90.0

        if rate_per_90 is None or rate_per_90 <= 0:
            return None

        avg_minutes: float = 72.0
        if stats.minutes is not None and mp > 0:
            avg_minutes = max(55.0, min(93.0, float(stats.minutes) / float(mp)))

        lam = rate_per_90 * (avg_minutes / 90.0)
        return max(0.0, lam)

    def _player_expected_shots_for_match(self, match: Match, player_id: int) -> float | None:
        """Esperanza de tiros (no sólo a puerta) por partido, vía temporada."""
        season = self.session.scalars(
            select(Season)
            .where(Season.league_id == match.league_id)
            .order_by(Season.year_start.desc())
            .limit(1)
        ).first()
        if season is None:
            return None
        stats = self.session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == player_id,
                PlayerSeasonStats.season_id == season.id,
            )
        )
        if stats is None:
            return None
        mp = max(int(stats.matches_played or 0), 1)
        rate_per_90: float | None = None
        if stats.shots_per_90 is not None and float(stats.shots_per_90) > 0:
            rate_per_90 = float(stats.shots_per_90)
        elif stats.minutes is not None and stats.minutes > 0 and stats.shots is not None:
            rate_per_90 = (stats.shots / float(stats.minutes)) * 90.0
        elif stats.shots is not None and stats.starts is not None and stats.starts > 0:
            mins_per_start = float(stats.minutes or 0) / float(stats.starts)
            mins_per_start = max(55.0, min(95.0, mins_per_start or 76.0))
            rate_per_90 = (stats.shots / mins_per_start) * 90.0

        if rate_per_90 is None or rate_per_90 <= 0:
            return None
        avg_minutes: float = 72.0
        if stats.minutes is not None and mp > 0:
            avg_minutes = max(55.0, min(93.0, float(stats.minutes) / float(mp)))
        lam = rate_per_90 * (avg_minutes / 90.0)
        return max(0.0, lam)

    def _player_goals_lambda_for_match(self, match: Match, player_id: int) -> float | None:
        season = self.session.scalars(
            select(Season)
            .where(Season.league_id == match.league_id)
            .order_by(Season.year_start.desc())
            .limit(1)
        ).first()
        if season is None:
            return None
        stats = self.session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == player_id,
                PlayerSeasonStats.season_id == season.id,
            )
        )
        if stats is None or stats.goals is None:
            return None
        mp = max(int(stats.matches_played or 0), 1)
        gp90: float | None = None
        if stats.minutes is not None and stats.minutes > 0 and stats.goals is not None:
            gp90 = stats.goals * 90.0 / float(stats.minutes)
        else:
            gp90 = stats.goals / float(mp)

        avg_minutes: float = 72.0
        if stats.minutes is not None and mp > 0:
            avg_minutes = max(55.0, min(93.0, float(stats.minutes) / float(mp)))
        lam = gp90 * (avg_minutes / 90.0)
        if lam <= 0:
            return None
        return max(0.0, lam)

    def _player_cards_lambda_for_match(self, match: Match, player_id: int) -> float | None:
        season = self.session.scalars(
            select(Season)
            .where(Season.league_id == match.league_id)
            .order_by(Season.year_start.desc())
            .limit(1)
        ).first()
        if season is None:
            return None
        stats = self.session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == player_id,
                PlayerSeasonStats.season_id == season.id,
            )
        )
        if stats is None:
            return None
        mp = max(int(stats.matches_played or 0), 1)
        y = int(stats.yellow_cards or 0)
        r = int(stats.red_cards or 0)
        ev_c = float(y + r)
        cp90: float | None = None
        if stats.minutes is not None and stats.minutes > 0:
            cp90 = ev_c * 90.0 / float(stats.minutes)
        elif mp > 0:
            cp90 = ev_c / float(mp)
        if cp90 is None or cp90 <= 0:
            return None
        avg_minutes: float = 72.0
        if stats.minutes is not None and mp > 0:
            avg_minutes = max(55.0, min(93.0, float(stats.minutes) / float(mp)))
        lam = cp90 * (avg_minutes / 90.0)
        return max(0.0, lam)

    def _model_probability_player_shots(
        self,
        match: Match,
        *,
        player_id: int,
        outcome_name: str,
        outcome_point: Decimal | None,
    ) -> Decimal | None:
        side = _parse_player_prop_side(outcome_name, outcome_point)
        if side is None or outcome_point is None:
            return None
        mu = self._player_expected_shots_for_match(match, player_id)
        if mu is None or mu <= 0:
            return None
        po, pu = pm.probability_over_single_poisson(mu, float(outcome_point))
        if side == "over":
            return Decimal(str(po))
        return Decimal(str(pu))

    def _model_probability_player_goal_freq(
        self,
        match: Match,
        *,
        player_id: int,
        market_key: str,
    ) -> Decimal | None:
        lam = self._player_goals_lambda_for_match(match, player_id)
        if lam is None or lam <= 0:
            return None
        if market_key == "player_anytime_scorer":
            pyes, _pu = pm.probability_over_single_poisson(lam, 0.5)
            return Decimal(str(max(0.0, min(1.0, pyes))))
        if market_key == "player_to_score_2plus":
            p_two_plus, _ = pm.probability_over_single_poisson(lam, 1.5)
            return Decimal(str(max(0.0, min(1.0, p_two_plus))))
        return None

    def _model_probability_player_card(
        self,
        match: Match,
        *,
        player_id: int,
        outcome_name: str,
    ) -> Decimal | None:
        lam = self._player_cards_lambda_for_match(match, player_id)
        if lam is None or lam <= 0:
            return None
        pyes, _ = pm.probability_over_single_poisson(lam, 0.5)
        inferred = _infer_yes_no_label(outcome_name)
        side = inferred if inferred is not None else "yes"
        if side == "yes":
            return Decimal(str(max(0.0, min(1.0, pyes))))
        return Decimal(str(max(0.0, min(1.0, 1.0 - pyes))))

    def _model_probability_player_sot(
        self,
        match: Match,
        *,
        player_id: int,
        outcome_name: str,
        outcome_point: Decimal | None,
    ) -> Decimal | None:
        side = _parse_player_prop_side(outcome_name, outcome_point)
        if side is None or outcome_point is None:
            return None
        mu = self._player_expected_sot_for_match(match, player_id)
        if mu is None or mu <= 0:
            return None
        po, pu = pm.probability_over_single_poisson(mu, float(outcome_point))
        if side == "over":
            return Decimal(str(po))
        return Decimal(str(pu))

    def _model_probability(
        self,
        expectation: pm.MatchExpectation | None,
        match: Match,
        *,
        market_key: str,
        outcome_name: str,
        outcome_point: Decimal | None,
        home_team_name: str,
        away_team_name: str,
        player_id: int | None = None,
    ) -> Decimal | None:
        """Devuelve la probabilidad del modelo para el outcome dado, o ``None``
        si no aplica (mercado no soportado, sin expectation, etc.)."""
        league_sk = match.league.sport_key or ""
        if "basketball" in league_sk.lower():
            return self._basketball_model_probability(
                match,
                market_key=market_key,
                outcome_name=outcome_name,
                outcome_point=outcome_point,
            )
        mk = market_key
        if mk in PLAYER_SOT_MARKETS:
            if player_id is None:
                return None
            return self._model_probability_player_sot(
                match,
                player_id=player_id,
                outcome_name=outcome_name,
                outcome_point=outcome_point,
            )
        if mk in PLAYER_SHOT_MARKETS:
            if player_id is None:
                return None
            return self._model_probability_player_shots(
                match,
                player_id=player_id,
                outcome_name=outcome_name,
                outcome_point=outcome_point,
            )
        if mk in PLAYER_GOAL_FREQ_MARKETS:
            if player_id is None:
                return None
            return self._model_probability_player_goal_freq(
                match, player_id=player_id, market_key=mk,
            )
        if mk in PLAYER_CARD_MARKETS:
            if player_id is None:
                return None
            return self._model_probability_player_card(
                match, player_id=player_id, outcome_name=outcome_name,
            )
        if mk in {"team_home_totals_goals", "team_away_totals_goals"}:
            if expectation is None or outcome_point is None:
                return None
            lam_team = expectation.lambda_home if mk == "team_home_totals_goals" else expectation.lambda_away
            po, pu = pm.probability_over_single_poisson(lam_team, float(outcome_point))
            name = outcome_name.strip().lower()
            if name in OVER_OUTCOMES:
                return Decimal(str(po))
            if name in UNDER_OUTCOMES:
                return Decimal(str(pu))
            return None

        if expectation is None:
            return None

        if mk == "h2h":
            name = outcome_name.strip()
            if name.lower() in DRAW_OUTCOMES:
                return Decimal(str(expectation.p_draw))
            if name == home_team_name:
                return Decimal(str(expectation.p_home_win))
            if name == away_team_name:
                return Decimal(str(expectation.p_away_win))
            return None

        if mk == "totals" and outcome_point is not None:
            po, pu = pm.probability_over_total(
                expectation.lambda_home, expectation.lambda_away, float(outcome_point),
            )
            name = outcome_name.strip().lower()
            if name in OVER_OUTCOMES:
                return Decimal(str(po))
            if name in UNDER_OUTCOMES:
                return Decimal(str(pu))
            return None

        if mk == "btts":
            name = outcome_name.strip().lower()
            if name in BTTS_YES_OUTCOMES:
                return Decimal(str(expectation.p_btts_yes))
            if name in BTTS_NO_OUTCOMES:
                return Decimal(str(expectation.p_btts_no))
            return None

        if mk == "double_chance":
            return self._double_chance_probability(
                expectation,
                outcome_name=outcome_name,
                home_team_name=home_team_name,
                away_team_name=away_team_name,
            )

        return None

    @staticmethod
    def _double_chance_probability(
        expectation: pm.MatchExpectation,
        *,
        outcome_name: str,
        home_team_name: str,
        away_team_name: str,
    ) -> Decimal | None:
        """Mapea outcomes de Double Chance del Odds API al modelo.

        El feed usa nombres tipo ``"Real Madrid or Draw"``, ``"Draw or Espanyol"``,
        ``"Real Madrid or Espanyol"`` (en ese orden o el invertido).
        """
        name = outcome_name.strip().lower()
        h = home_team_name.strip().lower()
        a = away_team_name.strip().lower()

        has_home = h in name
        has_away = a in name
        has_draw = "draw" in name or "tie" in name

        if has_home and has_draw and not has_away:
            return Decimal(str(expectation.p_home_or_draw))
        if has_away and has_draw and not has_home:
            return Decimal(str(expectation.p_draw_or_away))
        if has_home and has_away and not has_draw:
            return Decimal(str(expectation.p_home_or_away))

        # Aliases comunes "1X" / "12" / "X2"
        if name in {"1x", "home or draw"}:
            return Decimal(str(expectation.p_home_or_draw))
        if name in {"12", "home or away", "no draw"}:
            return Decimal(str(expectation.p_home_or_away))
        if name in {"x2", "draw or away"}:
            return Decimal(str(expectation.p_draw_or_away))
        return None

    # -- Lógica por mercado -------------------------------------------------

    def _detect_market(
        self,
        match: Match,
        market_key: str,
        expectation: pm.MatchExpectation | None,
    ) -> list[ValueBet]:
        odds_rows = self._latest_odds_for_match(match.id, market_key)
        if not odds_rows:
            return []

        if market_key == "h2h":
            return self._detect_h2h(match, odds_rows, expectation)
        if market_key == "totals" or market_key in {
            "team_home_totals_goals",
            "team_away_totals_goals",
        }:
            return self._detect_totals(match, odds_rows, expectation, market_key)
        if market_key in {"btts", "double_chance"}:
            return self._detect_simple(match, odds_rows, market_key, expectation)
        return []

    def _latest_odds_for_match(
        self, match_id: int, market_key: str,
    ) -> list[Odds]:
        stmt = (
            select(Odds)
            .join(Odds.market)
            .join(Odds.bookmaker)
            .where(Odds.match_id == match_id)
            .where(Market.key == market_key)
            .options(joinedload(Odds.bookmaker), joinedload(Odds.market))
        )
        if self._bookmaker_allowlist:
            stmt = stmt.where(Bookmaker.key.in_(tuple(self._bookmaker_allowlist)))
        return list(self.session.scalars(stmt).all())

    def _latest_player_prop_odds(
        self, match_id: int, market_keys: tuple[str, ...],
    ) -> list[Odds]:
        stmt = (
            select(Odds)
            .join(Odds.market)
            .join(Odds.bookmaker)
            .where(Odds.match_id == match_id)
            .where(Market.key.in_(market_keys))
            .where(Odds.player_id.isnot(None))
            .options(
                joinedload(Odds.bookmaker),
                joinedload(Odds.market),
                joinedload(Odds.player),
            )
        )
        if self._bookmaker_allowlist:
            stmt = stmt.where(Bookmaker.key.in_(tuple(self._bookmaker_allowlist)))
        return list(self.session.scalars(stmt).all())

    def _detect_h2h(
        self, match: Match, odds_rows: list[Odds], expectation: pm.MatchExpectation | None,
    ) -> list[ValueBet]:
        by_bookmaker: dict[Bookmaker, dict[str, Odds]] = defaultdict(dict)
        for o in odds_rows:
            if o.bookmaker.key in IGNORED_BOOKMAKERS:
                continue
            if not _odds_row_price_ok(o):
                continue
            by_bookmaker[o.bookmaker][o.outcome_name] = o

        complete = {bm: outs for bm, outs in by_bookmaker.items() if len(outs) >= 3}
        if not complete:
            return []

        try:
            sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
        except ValueError:
            return []
        sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
        fair_probs = _fair_probabilities_safe(sharp_odds_map)
        if fair_probs is None:
            return []

        return self._build_value_bets(
            match=match,
            market_key="h2h",
            by_bookmaker=complete,
            fair_probs=fair_probs,
            sharp_bookmaker=sharp_bm,
            sharp_margin=sharp_margin,
            outcome_point=None,
            expectation=expectation,
        )

    def _detect_totals(
        self,
        match: Match,
        odds_rows: list[Odds],
        expectation: pm.MatchExpectation | None,
        market_key: str = "totals",
    ) -> list[ValueBet]:
        by_point: dict[Decimal, list[Odds]] = defaultdict(list)
        for o in odds_rows:
            if o.bookmaker.key in IGNORED_BOOKMAKERS:
                continue
            if o.outcome_point is None:
                continue
            if not _odds_row_price_ok(o):
                continue
            by_point[o.outcome_point].append(o)

        all_bets: list[ValueBet] = []
        for point, point_rows in by_point.items():
            by_bookmaker: dict[Bookmaker, dict[str, Odds]] = defaultdict(dict)
            for o in point_rows:
                by_bookmaker[o.bookmaker][o.outcome_name] = o

            complete = {bm: outs for bm, outs in by_bookmaker.items() if len(outs) >= 2}
            if not complete:
                continue

            try:
                sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
            except ValueError:
                continue
            sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
            fair_probs = _fair_probabilities_safe(sharp_odds_map)
            if fair_probs is None:
                continue

            all_bets.extend(self._build_value_bets(
                match=match,
                market_key=market_key,
                by_bookmaker=complete,
                fair_probs=fair_probs,
                sharp_bookmaker=sharp_bm,
                sharp_margin=sharp_margin,
                outcome_point=point,
                expectation=expectation,
            ))
        return all_bets

    def _detect_player_sot_props(
        self,
        match: Match,
        expectation: pm.MatchExpectation | None,
    ) -> list[ValueBet]:
        odds_rows = self._latest_player_prop_odds(match.id, ("player_sot", "player_sot_alt"))
        if not odds_rows:
            return []
        by_grp: dict[tuple[int, Decimal, str], list[Odds]] = defaultdict(list)
        for o in odds_rows:
            if o.bookmaker.key in IGNORED_BOOKMAKERS:
                continue
            if o.player_id is None or o.outcome_point is None:
                continue
            if not _odds_row_price_ok(o):
                continue
            by_grp[(o.player_id, o.outcome_point, o.market.key)].append(o)

        all_bets: list[ValueBet] = []
        for _pid_key, grp_rows in by_grp.items():
            by_bookmaker: dict[Bookmaker, dict[str, Odds]] = defaultdict(dict)
            for o in grp_rows:
                by_bookmaker[o.bookmaker][o.outcome_name] = o
            player_id_grp, outcome_pt, mk = _pid_key
            complete = {
                bm: outs for bm, outs in by_bookmaker.items()
                if len(outs) >= 2
            }
            if not complete:
                continue

            try:
                sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
            except ValueError:
                continue
            sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
            fair_probs = _fair_probabilities_safe(sharp_odds_map)
            if fair_probs is None:
                continue

            all_bets.extend(
                self._build_value_bets(
                    match=match,
                    market_key=mk,
                    by_bookmaker=complete,
                    fair_probs=fair_probs,
                    sharp_bookmaker=sharp_bm,
                    sharp_margin=sharp_margin,
                    outcome_point=outcome_pt,
                    expectation=expectation,
                    player_id=player_id_grp,
                )
            )
        return all_bets

    def _detect_simple(
        self,
        match: Match,
        odds_rows: list[Odds],
        market_key: str,
        expectation: pm.MatchExpectation | None,
    ) -> list[ValueBet]:
        """Detector para mercados sin punto y con N fijo de outcomes (BTTS=2, DC=3).

        Funciona igual que ``_detect_h2h`` pero parametrizable por número
        mínimo de outcomes que debe tener el bookmaker para considerarlo
        completo.
        """
        min_outcomes = 3 if market_key == "double_chance" else 2

        by_bookmaker: dict[Bookmaker, dict[str, Odds]] = defaultdict(dict)
        for o in odds_rows:
            if o.bookmaker.key in IGNORED_BOOKMAKERS:
                continue
            if not _odds_row_price_ok(o):
                continue
            by_bookmaker[o.bookmaker][o.outcome_name] = o

        complete = {bm: outs for bm, outs in by_bookmaker.items() if len(outs) >= min_outcomes}
        if not complete:
            return []

        try:
            sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
        except ValueError:
            return []
        sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
        fair_probs = _fair_probabilities_safe(sharp_odds_map)
        if fair_probs is None:
            return []

        return self._build_value_bets(
            match=match,
            market_key=market_key,
            by_bookmaker=complete,
            fair_probs=fair_probs,
            sharp_bookmaker=sharp_bm,
            sharp_margin=sharp_margin,
            outcome_point=None,
            expectation=expectation,
        )

    def _find_sharpest(
        self, by_bookmaker: dict[Bookmaker, dict[str, Odds]],
    ) -> tuple[Bookmaker, dict[str, Odds], Decimal]:
        scored = [
            (bm, outs, vc.bookmaker_margin([o.price for o in outs.values()]))
            for bm, outs in by_bookmaker.items()
        ]
        scored.sort(key=lambda t: t[2])
        return scored[0]

    # -- Construcción de ValueBets ----------------------------------------

    def _build_value_bets(
        self,
        *,
        match: Match,
        market_key: str,
        by_bookmaker: dict[Bookmaker, dict[str, Odds]],
        fair_probs: dict[str, Decimal],
        sharp_bookmaker: Bookmaker,
        sharp_margin: Decimal,
        outcome_point: Decimal | None,
        expectation: pm.MatchExpectation | None,
        player_id: int | None = None,
    ) -> list[ValueBet]:
        bets: list[ValueBet] = []
        for bm, outcomes in by_bookmaker.items():
            if not self.official_pipeline and bm.id == sharp_bookmaker.id:
                continue
            for outcome_name, odds_row in outcomes.items():
                # Antes que nada: una cuota > MAX_RECOMMENDABLE_ODDS nunca es candidata.
                # (Sí cuenta arriba para quitar el margen: el mercado necesita todas sus cuotas.)
                if not vc.is_recommendable_odds(odds_row.price):
                    continue
                fair_prob = fair_probs.get(outcome_name)
                if fair_prob is None:
                    continue
                try:
                    ev_market = vc.expected_value(odds_row.price, fair_prob)
                except ValueError:
                    continue

                model_prob = self._model_probability(
                    expectation,
                    match,
                    market_key=market_key,
                    outcome_name=outcome_name,
                    outcome_point=outcome_point,
                    home_team_name=match.home_team.name,
                    away_team_name=match.away_team.name,
                    player_id=player_id,
                )
                model_ev: Decimal | None = None
                model_fair_odds: Decimal | None = None
                model_agrees: bool | None = None
                if model_prob is not None and model_prob > Decimal("0"):
                    try:
                        model_ev = vc.expected_value(odds_row.price, model_prob)
                    except ValueError:
                        model_ev = None
                        model_fair_odds = None
                        model_agrees = None
                    if model_ev is not None:
                        model_fair_odds = Decimal("1") / model_prob
                        model_agrees = model_ev >= self.min_ev_model

                league_sk = (match.league.sport_key or "").lower()
                if (
                    market_key == "h2h"
                    and self._h2h_min_model_prob > Decimal("0")
                    and "basketball" not in league_sk
                ):
                    if model_prob is None or model_prob < self._h2h_min_model_prob:
                        continue

                if self.official_pipeline:
                    if model_ev is None or model_ev < self.min_ev_model:
                        continue
                elif ev_market < self.min_ev:
                    continue

                # Filtro estricto si se requiere acuerdo
                if self.require_model_agreement:
                    if model_prob is None or model_agrees is not True:
                        continue

                fair_odds = Decimal("1") / fair_prob if fair_prob > 0 else Decimal("0")
                plname: str | None = None
                if player_id is not None:
                    pl = getattr(odds_row, "player", None)
                    if pl is not None:
                        plname = pl.name
                bets.append(ValueBet(
                    match_id=match.id,
                    league=match.league.name,
                    home_team=match.home_team.name,
                    away_team=match.away_team.name,
                    commence_time=match.commence_time,
                    market_key=market_key,
                    outcome_name=outcome_name,
                    outcome_point=outcome_point,
                    bookmaker_key=bm.key,
                    bookmaker_title=bm.title,
                    offered_odds=odds_row.price,
                    fair_probability=fair_prob,
                    fair_odds=fair_odds,
                    ev=ev_market,
                    sharp_bookie_key=sharp_bookmaker.key,
                    sharp_bookie_margin=sharp_margin,
                    model_probability=model_prob,
                    model_fair_odds=model_fair_odds,
                    model_ev=model_ev,
                    model_agrees=model_agrees,
                    confidence=_compute_confidence(
                        ev_market=ev_market,
                        ev_model=model_ev,
                        min_ev=self.min_ev,
                        official_pipeline=self.official_pipeline,
                    ),
                    player_name=plname,
                ))
        return bets


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _to_averages(stats: TeamSeasonStats) -> pm.TeamLeagueAverages | None:
    if not stats.matches_played or stats.matches_played <= 0:
        return None
    if stats.goals_for is None or stats.goals_against is None:
        return None
    return pm.TeamLeagueAverages(
        matches_played=stats.matches_played,
        goals_for_per_match=stats.goals_for / stats.matches_played,
        goals_against_per_match=stats.goals_against / stats.matches_played,
    )


def _compute_confidence(
    *,
    ev_market: Decimal,
    ev_model: Decimal | None,
    min_ev: Decimal,
    official_pipeline: bool = False,
) -> str:
    """Etiqueta cualitativa de confianza usando ambos EVs.

    * ``unknown``: no hay modelo (sin stats del partido).
    * ``low``: el modelo está en contra (``ev_model < min_ev``).
    * ``medium``: ambos EV ≥ min_ev pero ninguno es muy alto.
    * ``high``: ambos EV ≥ ``2 * min_ev`` (apuesta robusta a doble criterio).
    """
    if official_pipeline:
        if ev_model is None:
            return "unknown"
        threshold_high = max(min_ev * Decimal("2"), Decimal("0.05"))
        if ev_model < min_ev:
            return "low"
        if ev_model >= threshold_high:
            return "high"
        return "medium"

    if ev_model is None:
        return "unknown"
    if ev_model < min_ev:
        return "low"
    threshold_high = max(min_ev * Decimal("2"), Decimal("0.05"))
    if ev_market >= threshold_high and ev_model >= threshold_high:
        return "high"
    return "medium"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _configurar_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _formatear_outcome(b: ValueBet) -> str:
    if b.market_key == "totals":
        return f"{b.outcome_name} {b.outcome_point}"
    return b.outcome_name


def _imprimir_value_bets(bets: list[ValueBet], top: int = 30) -> None:
    log = logging.getLogger(__name__)
    log.info("=" * 110)
    log.info("TOP %d VALUE BETS DETECTADAS (de %d totales)", min(top, len(bets)), len(bets))
    log.info("=" * 110)
    if not bets:
        log.info("Ninguna value bet supera el umbral mínimo")
        return
    log.info(
        "%-28s %-22s %-10s %-7s %-9s %-9s %-9s %s",
        "PARTIDO", "MERCADO/OUTCOME", "BOOKIE",
        "CUOTA", "EV mkt", "EV mod", "CONF", "FECHA",
    )
    log.info("-" * 110)
    for b in bets[:top]:
        partido = f"{b.home_team} vs {b.away_team}"
        outcome = _formatear_outcome(b)
        ev_model_str = f"{float(b.model_ev) * 100:+6.2f}%" if b.model_ev is not None else "  n/a "
        log.info(
            "%-28s %-22s %-10s %-7s %+8.2f%% %-9s %-9s %s",
            partido[:28],
            f"{b.market_key} {outcome}"[:22],
            b.bookmaker_key[:10],
            f"{b.offered_odds:.2f}",
            float(b.ev) * 100,
            ev_model_str,
            b.confidence,
            b.commence_time.strftime("%Y-%m-%d %H:%M"),
        )
    log.info("=" * 110)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detecta value bets en partidos próximos.")
    parser.add_argument("--min-ev", type=float, default=0.02,
                        help="EV mínimo del MERCADO (default 0.02 = 2%%)")
    parser.add_argument("--min-ev-model", type=float, default=0.0,
                        help="EV mínimo del MODELO (default 0.0)")
    parser.add_argument("--require-model", action="store_true",
                        help="Solo apuestas que también pasan el criterio del modelo Poisson.")
    parser.add_argument("--horizon-hours", type=int, default=DEFAULT_HORIZON_HOURS,
                        help="Horizonte en horas (default: 14 días)")
    parser.add_argument("--league", default=None,
                        help="Sport key específico (ej. soccer_spain_la_liga)")
    parser.add_argument("--top", type=int, default=30, help="Cuántas mostrar")
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        detector = ValueDetector(
            session,
            min_ev=Decimal(str(args.min_ev)),
            min_ev_model=Decimal(str(args.min_ev_model)),
            require_model_agreement=args.require_model,
        )
        bets = detector.detect_upcoming(
            horizon_hours=args.horizon_hours,
            league_sport_key=args.league,
        )

    _imprimir_value_bets(bets, top=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
