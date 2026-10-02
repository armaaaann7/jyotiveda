"""Flexibility Marketplace — households offer flexibility; a fairness-aware MILP clears it.

    min  Σ_i  x_i · E_i · (p_i + λ · debt_i + κ_tier)            (pay-as-bid cost + fairness penalty)
    s.t. Σ_i  x_i · E_i  ≥  need_kWh                              (cover the Reliability Budget slice)
         Σ_{i∈h} x_i ≤ 1                                         (one activation per household per event)
         x_i ∈ {0,1}  (indivisible, e.g. EV session)  or  [0,1] (divisible, e.g. pump runtime)

Solved with HiGHS (scipy.optimize.milp). Settlement is uniform-price: every accepted kWh is paid the
marginal accepted price (incentive-compatible — bidding true cost is optimal), capped by the
DISCOM's DR price ceiling. Not a token market: settlement is INR via UPI, netted on the bill.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, Field
from scipy.optimize import Bounds, LinearConstraint, milp


class FlexAsset(StrEnum):
    EV = "ev"
    PUMP = "water_pump"
    WATER_HEATER = "water_heater"
    WASHING = "washing_machine"
    AC = "air_conditioner"
    REFRIGERATION = "commercial_refrigeration"


# discomfort premium (INR/kWh) — refrigeration and AC carry more inconvenience than a pump
ASSET_DISCOMFORT = {
    FlexAsset.PUMP: 0.0,
    FlexAsset.WATER_HEATER: 0.5,
    FlexAsset.WASHING: 1.0,
    FlexAsset.EV: 1.5,
    FlexAsset.AC: 3.0,
    FlexAsset.REFRIGERATION: 6.0,
}


class FlexOffer(BaseModel):
    id: str = Field(default_factory=lambda: f"FO-{uuid.uuid4().hex[:10]}")
    household_id: str
    transformer_id: str
    asset: FlexAsset
    energy_kwh: float = Field(gt=0, le=50)
    max_kw: float = Field(gt=0, le=25)
    window_start: datetime
    window_end: datetime
    min_incentive_inr: float = Field(ge=0, description="Minimum payment for the whole activation")
    divisible: bool = False
    rebound_ratio: float = Field(1.0, ge=0.8, le=1.3, description="kWh consumed later per kWh shifted")

    @property
    def price_per_kwh(self) -> float:
        return self.min_incentive_inr / self.energy_kwh


@dataclass
class ClearingResult:
    accepted: list[dict]
    rejected: list[dict]
    need_kwh: float
    cleared_kwh: float
    shortfall_kwh: float
    clearing_price_inr_per_kwh: float
    total_payment_inr: float
    status: str
    fairness_penalty_weight: float
    deferred_for_fairness: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def clear_market(
    offers: list[FlexOffer],
    need_kwh: float,
    debt_by_household: dict[str, float] | None = None,
    fairness_lambda: float = 4.0,
    price_cap_inr_per_kwh: float = 15.0,
) -> ClearingResult:
    debt_by_household = debt_by_household or {}
    eligible = [o for o in offers if o.price_per_kwh <= price_cap_inr_per_kwh]
    over_cap = [o for o in offers if o.price_per_kwh > price_cap_inr_per_kwh]
    if not eligible or need_kwh <= 0:
        return ClearingResult(
            [],
            [{"offer_id": o.id, "reason": "no need" if need_kwh <= 0 else "above price cap"} for o in offers],
            need_kwh,
            0.0,
            max(need_kwh, 0.0),
            0.0,
            0.0,
            "nothing-to-clear",
            fairness_lambda,
        )
    e = np.array([o.energy_kwh for o in eligible])
    debt = np.array([debt_by_household.get(o.household_id, 0.0) for o in eligible])
    unit_cost = (
        np.array([o.price_per_kwh + ASSET_DISCOMFORT[o.asset] for o in eligible]) + fairness_lambda * debt
    )
    c = unit_cost * e + 1e-3 * np.arange(len(eligible))  # deterministic tie-break
    target = min(need_kwh, float(e.sum()))
    rows = [e]
    lb, ub = [target], [np.inf]
    hh = sorted({o.household_id for o in eligible})
    for h in hh:
        rows.append(np.array([1.0 if o.household_id == h else 0.0 for o in eligible]))
        lb.append(0.0)
        ub.append(1.0)
    integrality = np.array([0 if o.divisible else 1 for o in eligible])
    res = milp(
        c=c,
        constraints=LinearConstraint(np.vstack(rows), lb, ub),
        integrality=integrality,
        bounds=Bounds(0, 1),
        options={"time_limit": 5.0},
    )
    if res.x is None:
        return ClearingResult(
            [], [], need_kwh, 0.0, need_kwh, 0.0, 0.0, f"infeasible:{res.message}", fairness_lambda
        )
    x = np.clip(res.x, 0, 1)
    x[x < 1e-6] = 0.0
    accepted_idx = np.nonzero(x > 0)[0]
    clearing = float(max((eligible[i].price_per_kwh for i in accepted_idx), default=0.0))
    clearing = min(clearing, price_cap_inr_per_kwh)
    accepted, rejected, deferred = [], [], []
    cheapest_rejected_debt = []
    for i, o in enumerate(eligible):
        if x[i] > 0:
            kwh = float(x[i] * o.energy_kwh)
            accepted.append(
                {
                    "offer_id": o.id,
                    "household_id": o.household_id,
                    "asset": o.asset.value,
                    "fraction": round(float(x[i]), 4),
                    "energy_kwh": round(kwh, 3),
                    "max_kw": o.max_kw,
                    "window_start": o.window_start.isoformat(),
                    "window_end": o.window_end.isoformat(),
                    "payment_inr": round(kwh * clearing, 2),
                    "rebound_kwh": round(kwh * o.rebound_ratio, 3),
                }
            )
        else:
            reason = "not needed (higher merit-order cost)"
            if debt[i] > 0 and o.price_per_kwh <= clearing:
                reason = "deferred: household carries high Fairness Debt; lower-burden flexibility available"
                deferred.append(o.household_id)
                cheapest_rejected_debt.append(o.id)
            rejected.append({"offer_id": o.id, "household_id": o.household_id, "reason": reason})
    rejected += [
        {"offer_id": o.id, "household_id": o.household_id, "reason": "above DR price cap"} for o in over_cap
    ]
    cleared = float(sum(a["energy_kwh"] for a in accepted))
    return ClearingResult(
        accepted=accepted,
        rejected=rejected,
        need_kwh=round(need_kwh, 3),
        cleared_kwh=round(cleared, 3),
        shortfall_kwh=round(max(need_kwh - cleared, 0.0), 3),
        clearing_price_inr_per_kwh=round(clearing, 3),
        total_payment_inr=round(sum(a["payment_inr"] for a in accepted), 2),
        status="optimal",
        fairness_penalty_weight=fairness_lambda,
        deferred_for_fairness=sorted(set(deferred)),
    )
