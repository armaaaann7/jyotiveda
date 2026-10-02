"""Forecast backends, strongest first:

1. Chronos2Backend   — Amazon Chronos-2 time-series foundation model (zero-shot, covariate-aware,
                        native quantiles). Works on a brand-new transformer with only days of history.
2. GBMQuantileBackend — LightGBM quantile regression on calendar + weather + seasonal-profile features.
                        The benchmark every foundation model must beat in the nightly backtest.
3. SeasonalNaiveBackend — empirical same-slot quantiles over the last k days. Never fails.
"""

from __future__ import annotations

import time
from functools import cached_property

import numpy as np
import pandas as pd
import structlog

from jyotiveda.forecasting.base import QUANTILES, QuantileForecast, enforce_monotone
from jyotiveda.observability import FALLBACK_ACTIVATIONS, FORECAST_LATENCY

log = structlog.get_logger(__name__)


def _future_index(history: pd.Series, horizon: int) -> pd.DatetimeIndex:
    freq = pd.infer_freq(history.index[-8:]) or "15min"
    return pd.date_range(history.index[-1], periods=horizon + 1, freq=freq)[1:]


def _slots_per_day(index: pd.DatetimeIndex) -> int:
    step = (index[1] - index[0]).total_seconds() if len(index) > 1 else 900
    return int(round(86400 / step))


class SeasonalNaiveBackend:
    name = "seasonal-naive"

    def __init__(self, days: int = 7) -> None:
        self.days = days

    def available(self) -> bool:
        return True

    def forecast(self, history, horizon, future_covariates=None, past_covariates=None) -> QuantileForecast:
        t0 = time.perf_counter()
        spd = _slots_per_day(history.index)
        vals = history.to_numpy(dtype=float)
        k = max(1, min(self.days, len(vals) // spd))
        mat = vals[-k * spd :].reshape(k, spd) if len(vals) >= spd else np.tile(vals.mean(), (1, spd))
        start = len(vals) % spd
        cols = (start + np.arange(horizon)) % spd
        samples = mat[:, cols]
        spread = np.maximum(samples.std(axis=0), 0.08 * samples.mean(axis=0) + 1e-6)
        med = np.median(samples, axis=0)
        q = {0.1: med - 1.2816 * spread, 0.5: med, 0.9: med + 1.2816 * spread}
        FORECAST_LATENCY.labels(self.name).observe(time.perf_counter() - t0)
        return QuantileForecast(
            _future_index(history, horizon), enforce_monotone(q), self.name, str(history.name)
        )


class GBMQuantileBackend:
    name = "lightgbm-quantile"

    def __init__(self, min_days: int = 5, cal_days: int = 2) -> None:
        self.min_days, self.cal_days = min_days, cal_days

    def available(self) -> bool:
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _features(index: pd.DatetimeIndex, profile: np.ndarray, cov: pd.DataFrame | None) -> pd.DataFrame:
        spd = len(profile)
        slot = ((index.hour * 60 + index.minute) // (1440 // spd)).to_numpy()
        f = pd.DataFrame(
            {
                "sin_day": np.sin(2 * np.pi * slot / spd),
                "cos_day": np.cos(2 * np.pi * slot / spd),
                "dow": index.dayofweek.to_numpy(),
                "profile": profile[slot % spd],
            },
            index=index,
        )
        if cov is not None:
            f = f.join(cov.reindex(index).ffill().bfill())
        return f

    def forecast(self, history, horizon, future_covariates=None, past_covariates=None) -> QuantileForecast:
        import lightgbm as lgb

        t0 = time.perf_counter()
        spd = _slots_per_day(history.index)
        if len(history) < self.min_days * spd:
            raise ValueError("insufficient history for GBM")
        vals = history.to_numpy(dtype=float)
        k = min(14, len(vals) // spd)
        profile = vals[-k * spd :].reshape(k, spd).mean(axis=0)
        # align profile to wall-clock slot
        first_slot = (history.index[-k * spd].hour * 60 + history.index[-k * spd].minute) // (1440 // spd)
        profile = np.roll(profile, first_slot)
        x_hist = self._features(history.index, profile, past_covariates)
        fidx = _future_index(history, horizon)
        x_fut = self._features(fidx, profile, future_covariates)
        common = [c for c in x_hist.columns if c in x_fut.columns]

        def fit_predict(x_tr: pd.DataFrame, y_tr: np.ndarray, x_te: pd.DataFrame) -> dict[float, np.ndarray]:
            out = {}
            for alpha in QUANTILES:
                params = {
                    "objective": "quantile",
                    "alpha": alpha,
                    "learning_rate": 0.05,
                    "num_leaves": 31,
                    "min_data_in_leaf": 10,
                    "verbose": -1,
                    "num_threads": 2,
                }
                model = lgb.train(params, lgb.Dataset(x_tr, y_tr), num_boost_round=200)
                out[alpha] = model.predict(x_te)
            return out

        # Split-conformal calibration (CQR): hold out the last `cal_days`, measure how far actuals
        # fall outside [p10, p90], and widen the final interval by that conformity quantile so the
        # 80% band has finite-sample coverage guarantees — the promotion gate checks exactly this.
        cal = self.cal_days * spd
        conformal_q = 0.0
        if len(vals) > cal + self.min_days * spd:
            qc = fit_predict(x_hist[common].iloc[:-cal], vals[:-cal], x_hist[common].iloc[-cal:])
            y_cal = vals[-cal:]
            scores = np.maximum(qc[0.1] - y_cal, y_cal - qc[0.9])
            level = min(np.ceil((cal + 1) * 0.8) / cal, 1.0)
            conformal_q = float(max(np.quantile(scores, level), 0.0))
        q = fit_predict(x_hist[common], vals, x_fut[common])
        q[0.1], q[0.9] = q[0.1] - conformal_q, q[0.9] + conformal_q
        FORECAST_LATENCY.labels(self.name).observe(time.perf_counter() - t0)
        return QuantileForecast(
            fidx,
            enforce_monotone(q),
            self.name,
            str(history.name),
            meta={"conformal_widening_kw": round(conformal_q, 3)},
        )


class Chronos2Backend:
    """Amazon Chronos-2 (encoder-only TSFM, group attention, past + future covariates).

    Weights are pulled from the Hugging Face hub (or a mirrored MinIO/S3 path in air-gapped DISCOM
    networks via JYOTIVEDA_CHRONOS_MODEL_ID=/models/chronos-2).
    """

    name = "chronos-2"

    def __init__(self, model_id: str = "amazon/chronos-2", device: str = "cpu") -> None:
        self.model_id, self.device = model_id, device
        self._failed = False

    def available(self) -> bool:
        if self._failed:
            return False
        try:
            import chronos  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    @cached_property
    def pipeline(self):  # pragma: no cover - requires torch + weights
        from chronos import Chronos2Pipeline

        return Chronos2Pipeline.from_pretrained(self.model_id, device_map=self.device)

    def forecast(self, history, horizon, future_covariates=None, past_covariates=None):  # pragma: no cover
        t0 = time.perf_counter()
        try:
            df = pd.DataFrame({"item_id": "s", "timestamp": history.index, "target": history.to_numpy(float)})
            fut = None
            if future_covariates is not None and past_covariates is not None:
                cols = [c for c in future_covariates.columns if c in past_covariates.columns]
                df = df.join(past_covariates[cols].reset_index(drop=True))
                fidx = _future_index(history, horizon)
                fut = future_covariates[cols].reindex(fidx).ffill().bfill().reset_index(names="timestamp")
                fut.insert(0, "item_id", "s")
            out = self.pipeline.predict_df(
                df, future_df=fut, prediction_length=horizon, quantile_levels=list(QUANTILES)
            )
            q = {}
            for a in QUANTILES:
                col = next(c for c in out.columns if str(c) in (str(a), f"{a:.1f}"))
                q[a] = out[col].to_numpy(dtype=float)
        except Exception:
            self._failed = True
            FALLBACK_ACTIVATIONS.labels("chronos-2").inc()
            log.exception("chronos2_failed_fallback")
            raise
        FORECAST_LATENCY.labels(self.name).observe(time.perf_counter() - t0)
        return QuantileForecast(
            _future_index(history, horizon), enforce_monotone(q), self.name, str(history.name)
        )
