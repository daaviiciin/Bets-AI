"""Tests del módulo de calibración del modelo Poisson."""

from __future__ import annotations

import math

from backend.services import poisson_model as pm
from backend.services.poisson_calibration import (
    CalibrationSample,
    compute_metrics,
    reliability_bins,
    grid_search_home_advantage,
)


def _expectation(
    *, p_home: float = 0.4, p_draw: float = 0.3, p_away: float = 0.3,
    p_o25: float = 0.55, p_btts: float = 0.55,
    lambda_home: float = 1.4, lambda_away: float = 1.1,
) -> pm.MatchExpectation:
    """Construye un MatchExpectation arbitrario para tests, sin pasar por Poisson."""
    return pm.MatchExpectation(
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        p_home_win=p_home,
        p_draw=p_draw,
        p_away_win=p_away,
        p_over_15=min(p_o25 + 0.2, 1.0),
        p_under_15=max(1.0 - (p_o25 + 0.2), 0.0),
        p_over_25=p_o25,
        p_under_25=1.0 - p_o25,
        p_over_35=max(p_o25 - 0.2, 0.0),
        p_under_35=min(1.0 - (p_o25 - 0.2), 1.0),
        p_btts_yes=p_btts,
        p_btts_no=1.0 - p_btts,
    )


def _sample(
    *, hs: int, asc: int,
    p_home: float = 0.4, p_draw: float = 0.3, p_away: float = 0.3,
    p_o25: float = 0.55, p_btts: float = 0.55,
    lambda_home: float = 1.4, lambda_away: float = 1.1,
) -> CalibrationSample:
    return CalibrationSample(
        home_team="A",
        away_team="B",
        home_score=hs,
        away_score=asc,
        expectation=_expectation(
            p_home=p_home, p_draw=p_draw, p_away=p_away,
            p_o25=p_o25, p_btts=p_btts,
            lambda_home=lambda_home, lambda_away=lambda_away,
        ),
    )


class TestSampleProperties:
    def test_outcome_home(self) -> None:
        assert _sample(hs=2, asc=0).outcome_1x2 == "H"

    def test_outcome_draw(self) -> None:
        assert _sample(hs=1, asc=1).outcome_1x2 == "D"

    def test_outcome_away(self) -> None:
        assert _sample(hs=0, asc=2).outcome_1x2 == "A"

    def test_total_goals(self) -> None:
        assert _sample(hs=2, asc=3).total_goals == 5

    def test_btts_yes(self) -> None:
        assert _sample(hs=1, asc=2).btts_yes is True

    def test_btts_no_when_one_zero(self) -> None:
        assert _sample(hs=2, asc=0).btts_yes is False


class TestComputeMetricsBasic:
    def test_empty_returns_zeros(self) -> None:
        rep = compute_metrics([])
        assert rep.n_samples == 0
        assert rep.brier_1x2 == 0.0

    def test_perfect_predictor_has_zero_brier(self) -> None:
        # Resultado: 2-1 → HOME wins, total=3 → over_2.5=True, btts=True
        # Predicción coherente con todo: p_home=1, p_o25=1, p_btts=1, λ=(2,1)
        s = _sample(hs=2, asc=1, p_home=1.0, p_draw=0.0, p_away=0.0,
                    p_o25=1.0, p_btts=1.0, lambda_home=2.0, lambda_away=1.0)
        rep = compute_metrics([s])
        assert math.isclose(rep.brier_1x2, 0.0, abs_tol=1e-12)
        assert math.isclose(rep.brier_btts, 0.0, abs_tol=1e-12)
        assert math.isclose(rep.brier_over_25, 0.0, abs_tol=1e-12)
        assert math.isclose(rep.mae_total_goals, 0.0, abs_tol=1e-12)
        assert rep.accuracy_1x2 == 1.0

    def test_uniform_predictor_brier_one_third(self) -> None:
        # Modelo "tira una moneda": 1/3 a cada outcome 1X2
        # Brier = (1 - 1/3)² / 3 + 2 * (1/3)² / 3 = (4/9)/3 + 2*(1/9)/3 = 6/27 = 2/9
        # Promediado sobre 3 clases ya está en _brier_multiclass
        s = _sample(hs=1, asc=0, p_home=1/3, p_draw=1/3, p_away=1/3)
        rep = compute_metrics([s])
        # Esperado: ((2/3)² + (1/3)² + (1/3)²) / 3 = (4/9 + 1/9 + 1/9)/3 = 6/27 = 2/9
        assert math.isclose(rep.brier_1x2, 2/9, abs_tol=1e-9)

    def test_observed_rates_match_real_distribution(self) -> None:
        samples = [
            _sample(hs=2, asc=0),  # H
            _sample(hs=1, asc=1),  # D
            _sample(hs=0, asc=3),  # A
            _sample(hs=2, asc=1),  # H
        ]
        rep = compute_metrics(samples)
        assert rep.observed_home_rate == 0.5
        assert rep.observed_draw_rate == 0.25
        assert rep.observed_away_rate == 0.25

    def test_accuracy_argmax_correct(self) -> None:
        # Predijo H con 0.6, real H → +1
        # Predijo D con 0.5, real H → 0
        samples = [
            _sample(hs=3, asc=0, p_home=0.6, p_draw=0.2, p_away=0.2),
            _sample(hs=1, asc=0, p_home=0.3, p_draw=0.5, p_away=0.2),
        ]
        rep = compute_metrics(samples)
        assert rep.accuracy_1x2 == 0.5

    def test_log_loss_handles_zero_probability(self) -> None:
        # Predijo p_home=0 pero ganó el local → log_loss debe ser finito (no -inf)
        s = _sample(hs=2, asc=0, p_home=0.0, p_draw=0.5, p_away=0.5)
        rep = compute_metrics([s])
        assert math.isfinite(rep.log_loss_1x2)
        assert rep.log_loss_1x2 > 5.0  # debería ser una penalización fuerte

    def test_mae_total_goals(self) -> None:
        # Predicción suma 2.5 goles, real fue 5 → MAE = 2.5
        s = _sample(hs=3, asc=2, lambda_home=1.4, lambda_away=1.1)
        rep = compute_metrics([s])
        assert math.isclose(rep.mae_total_goals, 2.5, abs_tol=1e-9)
        assert math.isclose(rep.mae_home_goals, 1.6, abs_tol=1e-9)
        assert math.isclose(rep.mae_away_goals, 0.9, abs_tol=1e-9)


class TestReliabilityBins:
    def test_perfectly_calibrated(self) -> None:
        # Para cada bucket [0, 0.1), [0.1, 0.2), ... mete N muestras donde
        # la frecuencia observada == probabilidad media.
        pairs: list[tuple[float, int]] = []
        for p in (0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95):
            n_pos = int(round(p * 100))
            n_neg = 100 - n_pos
            pairs.extend([(p, 1)] * n_pos)
            pairs.extend([(p, 0)] * n_neg)
        bins = reliability_bins(pairs, n_bins=10)
        assert len(bins) == 10
        for b in bins:
            assert math.isclose(b.mean_predicted, b.observed_frequency, abs_tol=0.01)

    def test_empty_input(self) -> None:
        assert reliability_bins([], n_bins=10) == []

    def test_skips_empty_buckets(self) -> None:
        bins = reliability_bins([(0.5, 1), (0.5, 0)], n_bins=10)
        assert len(bins) == 1
        assert bins[0].count == 2
        assert bins[0].lower == 0.5
        assert bins[0].observed_frequency == 0.5


class TestGridSearch:
    def test_iterates_over_values_and_calls_factory(self) -> None:
        calls: list[float] = []

        def factory(ha: float) -> list[CalibrationSample]:
            calls.append(ha)
            # Distintas predicciones según ha → distintas métricas
            return [_sample(hs=2, asc=0, p_home=min(ha / 1.5, 0.99))]

        results = grid_search_home_advantage(factory, values=(1.0, 1.1, 1.2))
        assert calls == [1.0, 1.1, 1.2]
        assert len(results) == 3
        assert all(r.report.n_samples == 1 for r in results)
        # Mayor home_advantage → mejor predicción → menor loss
        losses = [r.loss for r in results]
        assert losses == sorted(losses, reverse=True)
