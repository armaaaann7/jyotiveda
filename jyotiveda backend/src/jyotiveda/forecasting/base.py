"""Probabilistic forecasting contracts shared by every backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

QUANTILES = (0.1, 0.5, 0.9)
Z90 = 1.2815515655446004  # standard-normal 90th percentile


@dataclass
class QuantileForecast:
    index: pd.DatetimeIndex
    q: dict[float, np.ndarray]
    backend: str
    target: str
    meta: dict = field(default_factory=dict)

    @property
    def p10(self) -> np.ndarray:
        return self.q[0.1]

    @property
    def p50(self) -> np.ndarray:
        return self.q[0.5]

    @property
    def p90(self) -> np.ndarray:
        return self.q[0.9]

    def sample(self, z: np.ndarray | float) -> np.ndarray:
        """Inverse-CDF sample using a split-normal fitted to (p10, p50, p90). z may be scalar or per-slot."""
        z = np.asarray(z, dtype=float)
        lo = (self.p50 - self.p10) / Z90
        hi = (self.p90 - self.p50) / Z90
        return np.maximum(self.p50 + np.where(z < 0, z * lo, z * hi), 0.0)

    def to_records(self) -> list[dict]:
        return [
            {"ts": ts.isoformat(), "p10": float(a), "p50": float(b), "p90": float(c)}
            for ts, a, b, c in zip(self.index, self.p10, self.p50, self.p90, strict=True)
        ]


class ForecastBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def forecast(
        self,
        history: pd.Series,
        horizon: int,
        future_covariates: pd.DataFrame | None = None,
        past_covariates: pd.DataFrame | None = None,
    ) -> QuantileForecast: ...


def enforce_monotone(q: dict[float, np.ndarray]) -> dict[float, np.ndarray]:
    """Quantile crossing fix (sort across quantile axis) — needed for independently-trained GBM heads."""
    keys = sorted(q)
    stacked = np.sort(np.vstack([np.maximum(q[k], 0.0) for k in keys]), axis=0)
    return {k: stacked[i] for i, k in enumerate(keys)}
