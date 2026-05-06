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
   y calcular el **EV del modelo** contra la cuota ofrecida.
7. Devolver las apuestas que pasen los filtros, ordenadas por EV de mercado.

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
"""

from __future__ import annotations

import argparse
import logging
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
    Season,
    Team,
    TeamSeasonStats,
)
from backend.services import poisson_model as pm
from backend.services import value_calculator as vc

SUPPORTED_MARKETS = {"h2h", "totals", "btts", "double_chance"}
IGNORED_BOOKMAKERS = {"betfair_ex_eu"}
DEFAULT_MIN_EV = Decimal("0.0")
DEFAULT_MIN_EV_MODEL = Decimal("0.0")
DEFAULT_HORIZON_HOURS = 24 * 14

# Outcomes 1X2 — el feed de Odds API usa el nombre del equipo para H/A
# y la string ``"Draw"`` para el empate (en inglés siempre).
DRAW_OUTCOMES = {"draw", "tie"}
OVER_OUTCOMES = {"over"}
UNDER_OUTCOMES = {"under"}
BTTS_YES_OUTCOMES = {"yes", "both teams to score: yes"}
BTTS_NO_OUTCOMES = {"no", "both teams to score: no"}


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

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["commence_time"] = self.commence_time.isoformat()
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = float(v)
        return d


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
        log: logging.Logger | None = None,
    ) -> None:
        self.session = session
        self.min_ev = min_ev
        self.min_ev_model = min_ev_model
        self.require_model_agreement = require_model_agreement
        self.log = log or logging.getLogger(__name__)

        # Caches (se llenan al primer partido analizado de cada liga).
        # league_id -> {team_id: averages}
        self._team_avgs_by_league: dict[int, dict[int, pm.TeamLeagueAverages]] = {}
        # league_id -> list[averages] (todos los equipos de la liga, para μ)
        self._league_avgs: dict[int, list[pm.TeamLeagueAverages]] = {}
        # ρ del último CalibrationReport (lazy-loaded en el primer match).
        self._cached_rho: float | None = None

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
        all_bets.sort(key=lambda vb: vb.ev, reverse=True)
        return all_bets

    def detect_for_match(self, match: Match) -> list[ValueBet]:
        """Detecta value bets para un solo partido en todos los mercados soportados."""
        expectation = self._expectation_for_match(match)
        bets: list[ValueBet] = []
        bets.extend(self._detect_market(match, "h2h", expectation))
        bets.extend(self._detect_market(match, "totals", expectation))
        bets.extend(self._detect_market(match, "btts", expectation))
        bets.extend(self._detect_market(match, "double_chance", expectation))
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

    def _model_probability(
        self,
        expectation: pm.MatchExpectation | None,
        *,
        market_key: str,
        outcome_name: str,
        outcome_point: Decimal | None,
        home_team_name: str,
        away_team_name: str,
    ) -> Decimal | None:
        """Devuelve la probabilidad del modelo para el outcome dado, o ``None``
        si no aplica (mercado no soportado, sin expectation, etc.)."""
        if expectation is None:
            return None

        if market_key == "h2h":
            name = outcome_name.strip()
            if name.lower() in DRAW_OUTCOMES:
                return Decimal(str(expectation.p_draw))
            if name == home_team_name:
                return Decimal(str(expectation.p_home_win))
            if name == away_team_name:
                return Decimal(str(expectation.p_away_win))
            return None

        if market_key == "totals" and outcome_point is not None:
            po, pu = pm.probability_over_total(
                expectation.lambda_home, expectation.lambda_away, float(outcome_point),
            )
            name = outcome_name.strip().lower()
            if name in OVER_OUTCOMES:
                return Decimal(str(po))
            if name in UNDER_OUTCOMES:
                return Decimal(str(pu))
            return None

        if market_key == "btts":
            name = outcome_name.strip().lower()
            if name in BTTS_YES_OUTCOMES:
                return Decimal(str(expectation.p_btts_yes))
            if name in BTTS_NO_OUTCOMES:
                return Decimal(str(expectation.p_btts_no))
            return None

        if market_key == "double_chance":
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
        if market_key == "totals":
            return self._detect_totals(match, odds_rows, expectation)
        if market_key in {"btts", "double_chance"}:
            return self._detect_simple(match, odds_rows, market_key, expectation)
        return []

    def _latest_odds_for_match(
        self, match_id: int, market_key: str,
    ) -> list[Odds]:
        stmt = (
            select(Odds)
            .join(Odds.market)
            .where(Odds.match_id == match_id)
            .where(Market.key == market_key)
            .options(joinedload(Odds.bookmaker), joinedload(Odds.market))
        )
        return list(self.session.scalars(stmt).all())

    def _detect_h2h(
        self, match: Match, odds_rows: list[Odds], expectation: pm.MatchExpectation | None,
    ) -> list[ValueBet]:
        by_bookmaker: dict[Bookmaker, dict[str, Odds]] = defaultdict(dict)
        for o in odds_rows:
            if o.bookmaker.key in IGNORED_BOOKMAKERS:
                continue
            by_bookmaker[o.bookmaker][o.outcome_name] = o

        complete = {bm: outs for bm, outs in by_bookmaker.items() if len(outs) >= 3}
        if not complete:
            return []

        sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
        sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
        fair_probs = vc.fair_probabilities(sharp_odds_map)

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
        self, match: Match, odds_rows: list[Odds], expectation: pm.MatchExpectation | None,
    ) -> list[ValueBet]:
        by_point: dict[Decimal, list[Odds]] = defaultdict(list)
        for o in odds_rows:
            if o.bookmaker.key in IGNORED_BOOKMAKERS:
                continue
            if o.outcome_point is None:
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

            sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
            sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
            fair_probs = vc.fair_probabilities(sharp_odds_map)

            all_bets.extend(self._build_value_bets(
                match=match,
                market_key="totals",
                by_bookmaker=complete,
                fair_probs=fair_probs,
                sharp_bookmaker=sharp_bm,
                sharp_margin=sharp_margin,
                outcome_point=point,
                expectation=expectation,
            ))
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
            by_bookmaker[o.bookmaker][o.outcome_name] = o

        complete = {bm: outs for bm, outs in by_bookmaker.items() if len(outs) >= min_outcomes}
        if not complete:
            return []

        sharp_bm, sharp_outs, sharp_margin = self._find_sharpest(complete)
        sharp_odds_map = {name: o.price for name, o in sharp_outs.items()}
        fair_probs = vc.fair_probabilities(sharp_odds_map)

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
    ) -> list[ValueBet]:
        bets: list[ValueBet] = []
        for bm, outcomes in by_bookmaker.items():
            if bm.id == sharp_bookmaker.id:
                continue
            for outcome_name, odds_row in outcomes.items():
                fair_prob = fair_probs.get(outcome_name)
                if fair_prob is None:
                    continue
                ev_market = vc.expected_value(odds_row.price, fair_prob)
                if ev_market < self.min_ev:
                    continue

                # Modelo
                model_prob = self._model_probability(
                    expectation,
                    market_key=market_key,
                    outcome_name=outcome_name,
                    outcome_point=outcome_point,
                    home_team_name=match.home_team.name,
                    away_team_name=match.away_team.name,
                )
                model_ev: Decimal | None = None
                model_fair_odds: Decimal | None = None
                model_agrees: bool | None = None
                if model_prob is not None and model_prob > Decimal("0"):
                    model_ev = vc.expected_value(odds_row.price, model_prob)
                    model_fair_odds = Decimal("1") / model_prob
                    model_agrees = model_ev >= self.min_ev_model

                # Filtro estricto si se requiere acuerdo
                if self.require_model_agreement:
                    if model_prob is None or model_agrees is not True:
                        continue

                fair_odds = Decimal("1") / fair_prob if fair_prob > 0 else Decimal("0")
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
                    ),
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
) -> str:
    """Etiqueta cualitativa de confianza usando ambos EVs.

    * ``unknown``: no hay modelo (sin stats del partido).
    * ``low``: el modelo está en contra (``ev_model < min_ev``).
    * ``medium``: ambos EV ≥ min_ev pero ninguno es muy alto.
    * ``high``: ambos EV ≥ ``2 * min_ev`` (apuesta robusta a doble criterio).
    """
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
