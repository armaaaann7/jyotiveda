"""Forecast evaluation used by the nightly backtest and model-promotion gate."""

from __future__ import annotations

import numpy as np

from jyotiveda.forecasting.base import QuantileForecast


def pinball(y: np.ndarray, q: np.ndarray, alpha: float) -> float:
    d = y - q
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


def evaluate(fc: QuantileForecast, actual: np.ndarray) -> dict[str, float]:
    y = np.asarray(actual, dtype=float)[: len(fc.p50)]
    denom = max(float(np.mean(np.abs(y))), 1e-6)
    return {
        "mae": float(np.mean(np.abs(y - fc.p50))),
        "nmae": float(np.mean(np.abs(y - fc.p50)) / denom),
        "bias": float(np.mean(fc.p50 - y) / denom),
        "coverage_80": float(np.mean((y >= fc.p10) & (y <= fc.p90))),
        "crps_approx": float(np.mean([pinball(y, fc.q[a], a) for a in sorted(fc.q)]) * 2),
    }


def promotion_gate(candidate: dict[str, float], incumbent: dict[str, float], min_gain: float = 0.02) -> bool:
    """Promote only if CRPS improves by min_gain AND calibration stays within 70-90% coverage."""
    better = candidate["crps_approx"] <= incumbent["crps_approx"] * (1 - min_gain)
    calibrated = 0.7 <= candidate["coverage_80"] <= 0.9
    return better and calibrated
