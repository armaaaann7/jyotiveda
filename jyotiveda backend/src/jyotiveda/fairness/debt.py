"""Fairness Debt: a machine-readable, auditable ledger of who has carried the burden of flexibility.

debt_{t+1} = gamma * debt_t + w_income * (burden_t / baseline_t) - fair_share_t

* burden = shifted kWh * inconvenience + curtailed kWh * curtailment_weight (+ weighting by tier)
* baseline normalises by the household's own consumption, so a small home is not asked for the
  same absolute kWh as a large shop
* w_income > 1 for low-income homes: the same kWh hurts more
* the population-mean normalised burden is subtracted, so debt is zero-sum-ish and decays

The debt feeds (a) the flexibility market's merit order and (b) the curtailment water-filling,
so the same households are not asked repeatedly. Gini and Jain indices are published per DT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from jyotiveda.domain import Household, IncomeBand

INCOME_WEIGHT = {IncomeBand.LOW: 1.5, IncomeBand.MIDDLE: 1.0, IncomeBand.COMMERCIAL: 0.8}


@dataclass
class FairnessLedger:
    household_ids: list[str]
    income_weight: np.ndarray
    gamma: float = 0.97  # per-slot decay (~8 h half-life at 15-min slots)
    debt: np.ndarray = field(init=False)
    events: np.ndarray = field(init=False)
    shifted_kwh: np.ndarray = field(init=False)
    curtailed_kwh: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        n = len(self.household_ids)
        self.debt = np.zeros(n)
        self.events = np.zeros(n, dtype=int)
        self.shifted_kwh = np.zeros(n)
        self.curtailed_kwh = np.zeros(n)

    @classmethod
    def for_households(cls, households: list[Household], **kw) -> FairnessLedger:
        return cls(
            [h.id for h in households], np.array([INCOME_WEIGHT[h.income_band] for h in households]), **kw
        )

    def update(
        self,
        shifted_kwh: np.ndarray,
        curtailed_kwh: np.ndarray,
        baseline_kwh: np.ndarray,
        inconvenience: float = 1.0,
        curtail_weight: float = 2.5,
    ) -> np.ndarray:
        burden = inconvenience * shifted_kwh + curtail_weight * curtailed_kwh
        norm = self.income_weight * burden / np.maximum(baseline_kwh, 0.05)
        share = norm.mean() if norm.size else 0.0
        self.debt = np.maximum(self.gamma * self.debt + norm - share, 0.0)
        self.events += (burden > 1e-6).astype(int)
        self.shifted_kwh += shifted_kwh
        self.curtailed_kwh += curtailed_kwh
        return self.debt

    def weights(self) -> np.ndarray:
        """Curtailment/selection weights in (0,1]: high debt => asked less."""
        return 1.0 / (1.0 + self.debt)

    def level(self, i: int) -> str:
        q = np.quantile(self.debt, [0.5, 0.85]) if self.debt.any() else (0.0, 0.0)
        d = self.debt[i]
        return "HIGH" if d > q[1] and d > 0 else ("MEDIUM" if d > q[0] and d > 0 else "LOW")

    def snapshot(self) -> list[dict]:
        return [
            {
                "household_id": hid,
                "debt": round(float(self.debt[i]), 4),
                "level": self.level(i),
                "dr_events": int(self.events[i]),
                "energy_shifted_kwh": round(float(self.shifted_kwh[i]), 3),
                "energy_curtailed_kwh": round(float(self.curtailed_kwh[i]), 3),
            }
            for i, hid in enumerate(self.household_ids)
        ]


def gini(x: np.ndarray) -> float:
    x = np.sort(np.asarray(x, dtype=float))
    if x.size == 0 or x.sum() == 0:
        return 0.0
    n = x.size
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum()))


def jain_index(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    return float(x.sum() ** 2 / (x.size * (x**2).sum())) if (x**2).sum() > 0 else 1.0
