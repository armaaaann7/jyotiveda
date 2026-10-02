"""forecast-service: demand, solar and Reliability-Gap forecasts, 15 min – 72 h, with uncertainty.

Routing: `auto` tries Chronos-2 → LightGBM → seasonal-naive. Every forecast records which backend
produced it so the nightly backtest (forecasting/metrics.py) can compare skill per model and the
registry can promote/demote (shadow → canary → production).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

from jyotiveda.config import Settings
from jyotiveda.forecasting.backends import Chronos2Backend, GBMQuantileBackend, SeasonalNaiveBackend
from jyotiveda.forecasting.base import QuantileForecast, enforce_monotone
from jyotiveda.observability import FALLBACK_ACTIVATIONS

log = structlog.get_logger(__name__)


@dataclass
class GapForecast:
    """Reliability Gap = demand - solar - available grid supply (kW, > 0 means shortage)."""

    index: pd.DatetimeIndex
    p_shortage: np.ndarray  # probability that gap > 0 in each slot
    expected_shortage_kw: np.ndarray
    p90_shortage_kw: np.ndarray
    expected_shortage_kwh: float
    p90_shortage_kwh: float
    windows: list[dict]

    def to_dict(self) -> dict:
        return {
            "expected_shortage_kwh": round(self.expected_shortage_kwh, 2),
            "p90_shortage_kwh": round(self.p90_shortage_kwh, 2),
            "windows": self.windows,
            "series": [
                {
                    "ts": ts.isoformat(),
                    "p_shortage": round(float(p), 3),
                    "expected_kw": round(float(e), 2),
                    "p90_kw": round(float(q), 2),
                }
                for ts, p, e, q in zip(
                    self.index, self.p_shortage, self.expected_shortage_kw, self.p90_shortage_kw, strict=True
                )
            ],
        }


class ForecastService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        chain = {
            "chronos2": [Chronos2Backend(settings.chronos_model_id, settings.chronos_device)],
            "gbm": [GBMQuantileBackend()],
            "seasonal": [SeasonalNaiveBackend()],
        }
        if settings.forecast_backend == "auto":
            self.chain = [*chain["chronos2"], *chain["gbm"], *chain["seasonal"]]
        else:
            self.chain = [*chain[settings.forecast_backend], SeasonalNaiveBackend()]

    def forecast(
        self,
        history: pd.Series,
        horizon: int,
        future_covariates: pd.DataFrame | None = None,
        past_covariates: pd.DataFrame | None = None,
    ) -> QuantileForecast:
        for backend in self.chain:
            if not backend.available():
                continue
            try:
                return backend.forecast(history, horizon, future_covariates, past_covariates)
            except Exception as exc:  # degrade gracefully, never block the control loop
                FALLBACK_ACTIVATIONS.labels(f"forecast:{backend.name}").inc()
                log.warning("forecast_backend_failed", backend=backend.name, error=str(exc))
        raise RuntimeError("no forecast backend available")

    @staticmethod
    def solar(
        index: pd.DatetimeIndex,
        clear_sky_kw: np.ndarray,
        cloud_p50: np.ndarray,
        cloud_sigma: np.ndarray | float = 0.12,
    ) -> QuantileForecast:
        """Physics-informed solar: clear-sky PV x AI-weather cloud-transmittance forecast with uncertainty."""
        sig = np.broadcast_to(np.asarray(cloud_sigma, dtype=float), cloud_p50.shape)
        q = {
            0.1: clear_sky_kw * np.clip(cloud_p50 - 1.2816 * sig, 0, 1),
            0.5: clear_sky_kw * np.clip(cloud_p50, 0, 1),
            0.9: clear_sky_kw * np.clip(cloud_p50 + 1.2816 * sig, 0, 1),
        }
        return QuantileForecast(index, enforce_monotone(q), "clear-sky x weather-ai", "solar_kw")

    @staticmethod
    def gap(
        demand: QuantileForecast,
        solar: QuantileForecast,
        grid_cap_kw: np.ndarray,
        battery_kw: float = 0.0,
        n_samples: int = 400,
        rho: float = -0.6,
        seed: int = 0,
    ) -> GapForecast:
        """Monte-Carlo gap with a Gaussian copula: demand and solar errors negatively correlated
        (cloudy + hot/humid evenings are the bad case). Per-sample errors are persistent in time."""
        rng = np.random.default_rng(seed)
        z1 = rng.standard_normal(n_samples)
        z2 = rho * z1 + np.sqrt(1 - rho**2) * rng.standard_normal(n_samples)
        d = np.stack([demand.sample(z) for z in z1])
        s = np.stack([solar.sample(z) for z in z2])
        g = np.maximum(d - s - grid_cap_kw[None, :] - battery_kw, 0.0)
        dt_h = (demand.index[1] - demand.index[0]).total_seconds() / 3600 if len(demand.index) > 1 else 0.25
        p = (g > 0.5).mean(axis=0)
        exp_kw = g.mean(axis=0)
        p90 = np.quantile(g, 0.9, axis=0)
        windows, start = [], None
        for i, flag in enumerate(np.append(p >= 0.3, False)):
            if flag and start is None:
                start = i
            elif not flag and start is not None:
                windows.append(
                    {
                        "start": demand.index[start].isoformat(),
                        "end": (demand.index[i - 1] + (demand.index[1] - demand.index[0])).isoformat(),
                        "peak_probability": round(float(p[start:i].max()), 3),
                        "expected_kwh": round(float(exp_kw[start:i].sum() * dt_h), 2),
                        "p90_kwh": round(float(p90[start:i].sum() * dt_h), 2),
                    }
                )
                start = None
        return GapForecast(
            demand.index,
            p,
            exp_kw,
            p90,
            float(g.sum(axis=1).mean() * dt_h),
            float(np.quantile(g.sum(axis=1) * dt_h, 0.9)),
            windows,
        )
