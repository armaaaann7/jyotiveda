"""Nightly forecast backtest + promotion gate (runs as a Kubernetes CronJob).

    python -m jyotiveda.ml_backtest --days 30

Rolls a 24 h forecast origin across the last N days for every available backend, scores
nMAE / CRPS / 80% coverage, and applies the promotion gate (CRPS -2% and calibrated coverage).
With the `ml` extra, Chronos-2 is included; results are logged to MLflow when MLFLOW_TRACKING_URI is set.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from jyotiveda.config import Settings
from jyotiveda.forecasting.backends import Chronos2Backend, GBMQuantileBackend, SeasonalNaiveBackend
from jyotiveda.forecasting.metrics import evaluate, promotion_gate
from jyotiveda.twin.neighbourhood import build_neighbourhood
from jyotiveda.twin.profiles import household_demand, slot_index


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--history-days", type=int, default=14)
    args = ap.parse_args(argv)
    s = Settings()
    nb = build_neighbourhood()
    total_days = args.days + args.history_days
    idx = slot_index(datetime(2026, 6, 1), total_days * 96)
    heat = 1 + 0.15 * np.sin(np.arange(total_days) / 5)
    series = pd.Series(
        np.concatenate(
            [
                household_demand(
                    nb.households, idx[d * 96 : (d + 1) * 96], seed=d, heat_index=float(heat[d])
                ).total.sum(1)
                for d in range(total_days)
            ]
        ),
        index=idx,
        name="demand_kw",
    )
    # weather covariate (daily heat index -> temperature), known in the future from the weather service
    temp = pd.DataFrame({"temp_c": np.repeat(28 + 12 * (heat - 1), 96)}, index=idx)
    backends = [SeasonalNaiveBackend(), GBMQuantileBackend(), Chronos2Backend(s.chronos_model_id)]
    scores: dict[str, list[dict]] = {}
    for b in backends:
        if not b.available():
            continue
        for d in range(args.history_days, total_days):
            hist = series.iloc[: d * 96]
            actual = series.iloc[d * 96 : (d + 1) * 96].to_numpy()
            fc = b.forecast(hist, 96, future_covariates=temp, past_covariates=temp.iloc[: d * 96])
            scores.setdefault(b.name, []).append(evaluate(fc, actual))
    summary = {k: {m: float(np.mean([r[m] for r in v])) for m in v[0]} for k, v in scores.items()}
    incumbent = summary.get("seasonal-naive")
    for name, m in summary.items():
        m["promote_over_seasonal"] = bool(
            incumbent and name != "seasonal-naive" and promotion_gate(m, incumbent)
        )
    if os.getenv("MLFLOW_TRACKING_URI"):  # pragma: no cover
        import mlflow

        mlflow.set_experiment("jyotiveda-forecast-backtest")
        with mlflow.start_run(run_name=f"backtest-{datetime.now():%Y%m%d}"):
            for name, m in summary.items():
                mlflow.log_metrics({f"{name}.{k}": float(v) for k, v in m.items()})
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
