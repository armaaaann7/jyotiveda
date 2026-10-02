"""Reliability Budget — the core IP.

Converts a probabilistic Reliability Gap into an explicit, auditable allocation:

    required = risk-adjusted shortage (P90 by default)  for the scarcity window
    protect  = T0 + T1 energy in that window (never negotiable)
    allocate = required across resources in merit order of *true* cost:
               P2P rooftop surplus → battery (degradation + opportunity cost) → flexibility market
               → automatic T3 load shift → T4 lifeline-mode curtailment (last resort)

The budget is what a DISCOM engineer, an auditor or a resident can read. MPC then turns it into
a 15-minute schedule, and the safety shield has the last word on every physical command.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from jyotiveda.battery.model import BatteryEnvelope


@dataclass
class BudgetLine:
    resource: str
    energy_kwh: float
    unit_cost_inr: float
    note: str = ""


@dataclass
class ReliabilityBudget:
    transformer: str
    window_start: datetime
    window_end: datetime
    risk_level: str
    shortage_probability: float
    forecast_gap_kwh: float  # expected
    required_kwh: float  # risk-adjusted (P90)
    critical_load_kwh: float
    battery_available_kwh: float
    p2p_available_kwh: float
    flexibility_offered_kwh: float
    auto_shift_available_kwh: float
    curtailable_discretionary_kwh: float
    allocation: list[BudgetLine] = field(default_factory=list)
    uncovered_kwh: float = 0.0
    critical_loads_protected: bool = True
    lifeline_mode: bool = False
    expected_cost_inr: float = 0.0

    @property
    def flexibility_required_kwh(self) -> float:
        return sum(
            a.energy_kwh for a in self.allocation if a.resource in ("flexibility_market", "auto_load_shift")
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["window_start"], d["window_end"] = self.window_start.isoformat(), self.window_end.isoformat()
        d["flexibility_required_kwh"] = round(self.flexibility_required_kwh, 2)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 2)
        return d


def risk_level(p: float, p90_kwh: float, critical_kwh: float) -> str:
    if p90_kwh <= 0.5 or p < 0.1:
        return "LOW"
    if p < 0.4 and p90_kwh < 0.25 * max(critical_kwh, 1):
        return "MODERATE"
    if p90_kwh > max(critical_kwh, 1):
        return "CRITICAL"
    return "HIGH"


def build_budget(
    transformer: str,
    window_start: datetime,
    window_end: datetime,
    shortage_probability: float,
    expected_gap_kwh: float,
    p90_gap_kwh: float,
    critical_kwh: float,
    battery: BatteryEnvelope | None,
    battery_window_kwh_limit: float,
    p2p_kwh: float,
    flex_offered_kwh: float,
    flex_price_inr: float,
    auto_shift_kwh: float,
    discretionary_kwh: float,
    peak_tariff_inr: float = 7.8,
    risk_quantile: str = "p90",
) -> ReliabilityBudget:
    required = p90_gap_kwh if risk_quantile == "p90" else expected_gap_kwh
    batt = min(battery.usable_energy_kwh, battery_window_kwh_limit) if battery else 0.0
    batt_cost = (battery.degradation_cost_inr_per_kwh + peak_tariff_inr * 0.3) if battery else 0.0
    ladder = [
        ("p2p_solar", p2p_kwh, 4.0, "rooftop surplus shared within the DT (stored or shifted)"),
        ("battery", batt, batt_cost, "shared second-life LFP, degradation-priced"),
        (
            "flexibility_market",
            flex_offered_kwh,
            flex_price_inr,
            "household offers cleared by fairness-aware MILP",
        ),
        ("auto_load_shift", auto_shift_kwh, 3.0, "opt-in smart-plug / smart-meter scheduling of T3 loads"),
        (
            "lifeline_mode_curtailment",
            discretionary_kwh,
            25.0,
            "T4 comfort loads limited; T0/T1 always served",
        ),
    ]
    remaining = required
    lines: list[BudgetLine] = []
    cost = 0.0
    for name, avail, unit, note in ladder:
        if remaining <= 1e-6:
            break
        take = min(avail, remaining)
        if take > 1e-6:
            lines.append(BudgetLine(name, take, unit, note))
            cost += take * unit
            remaining -= take
    level = risk_level(shortage_probability, p90_gap_kwh, critical_kwh)
    return ReliabilityBudget(
        transformer=transformer,
        window_start=window_start,
        window_end=window_end,
        risk_level=level,
        shortage_probability=shortage_probability,
        forecast_gap_kwh=expected_gap_kwh,
        required_kwh=required,
        critical_load_kwh=critical_kwh,
        battery_available_kwh=batt,
        p2p_available_kwh=p2p_kwh,
        flexibility_offered_kwh=flex_offered_kwh,
        auto_shift_available_kwh=auto_shift_kwh,
        curtailable_discretionary_kwh=discretionary_kwh,
        allocation=lines,
        uncovered_kwh=max(remaining, 0.0),
        critical_loads_protected=remaining <= 1e-6,
        lifeline_mode=any(line.resource == "lifeline_mode_curtailment" for line in lines),
        expected_cost_inr=cost,
    )
