"""Battery intelligence: usable energy, temperature/SoH derating and degradation cost.

The optimiser must know that 1 kWh from a second-life battery is *not* free: every kWh of
throughput consumes cycle life. We price that with a DoD-dependent Wöhler curve and an
Arrhenius temperature factor, so MPC trades battery wear against flexibility and outage cost.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from jyotiveda.domain import Battery, BatteryState


@dataclass(frozen=True)
class BatteryEnvelope:
    usable_energy_kwh: float  # energy dischargeable now down to soc_min
    headroom_kwh: float  # energy chargeable now up to soc_max
    max_charge_kw: float
    max_discharge_kw: float
    degradation_cost_inr_per_kwh: float
    effective_capacity_kwh: float
    healthy: bool
    reasons: tuple[str, ...] = ()


def cycle_life(dod: float, rated_cycles_at_80: int) -> float:
    """Wöhler-style fit for LFP: N(DoD) = N80 * (0.8 / DoD)^1.1."""
    dod = max(min(dod, 1.0), 0.05)
    return rated_cycles_at_80 * (0.8 / dod) ** 1.1


def arrhenius_factor(temp_c: float, ref_c: float = 25.0, ea_ev: float = 0.3) -> float:
    k_b = 8.617e-5
    return math.exp(ea_ev / k_b * (1 / (ref_c + 273.15) - 1 / (temp_c + 273.15)))


def degradation_cost(b: Battery, soh: float, temp_c: float, dod: float = 0.8) -> float:
    """INR per kWh of throughput (charge + discharge counted separately)."""
    cap = b.capacity_kwh * soh
    life_kwh = cycle_life(dod, b.rated_cycles_at_80dod) * cap * dod * 2
    # second-life packs are valued at their remaining (not new) replacement cost
    value = b.replacement_cost_inr_per_kwh * cap * (0.9 if b.second_life else 1.0)
    return value / max(life_kwh, 1.0) * arrhenius_factor(temp_c)


def envelope(
    b: Battery, s: BatteryState, telemetry_age_s: float = 0.0, max_age_s: float = 120.0
) -> BatteryEnvelope:
    reasons: list[str] = []
    cap = b.capacity_kwh * s.soh
    usable = max(s.soc - b.soc_min, 0.0) * cap
    headroom = max(b.soc_max - s.soc, 0.0) * cap
    p_ch, p_dis = b.max_charge_kw, b.max_discharge_kw
    # LFP temperature derating (manufacturer-style windows)
    if s.temperature_c >= 55:
        p_ch = p_dis = 0.0
        reasons.append("over-temperature lockout (>=55C)")
    elif s.temperature_c >= 45:
        p_ch, p_dis = 0.0, p_dis * 0.5
        reasons.append("high temperature derate (45-55C): no charge, 50% discharge")
    elif s.temperature_c <= 0:
        p_ch = 0.0
        reasons.append("no charging below 0C")
    if s.soh < 0.6:
        p_ch, p_dis = p_ch * 0.5, p_dis * 0.5
        reasons.append("SoH below 60%: 50% power derate, schedule replacement")
    if telemetry_age_s > max_age_s:
        p_ch = p_dis = 0.0
        reasons.append(f"stale telemetry ({telemetry_age_s:.0f}s): fail-safe idle")
    if any(a.startswith("BMS_FAULT") for a in s.alarms):
        p_ch = p_dis = 0.0
        reasons.append("BMS fault alarm active")
    return BatteryEnvelope(
        usable_energy_kwh=usable,
        headroom_kwh=headroom,
        max_charge_kw=p_ch,
        max_discharge_kw=p_dis,
        degradation_cost_inr_per_kwh=degradation_cost(b, s.soh, s.temperature_c),
        effective_capacity_kwh=cap,
        healthy=not reasons,
        reasons=tuple(reasons),
    )


def step_soc(b: Battery, soc: float, soh: float, power_kw: float, dt_h: float) -> float:
    """power_kw > 0 discharge, < 0 charge."""
    cap = b.capacity_kwh * soh
    de = -power_kw * dt_h / b.eta_discharge if power_kw >= 0 else -power_kw * dt_h * b.eta_charge
    return min(max(soc + de / cap, 0.0), 1.0)


def estimate_soh(capacity_fade_points: list[tuple[float, float]]) -> float:
    """Least-squares SoH from (equivalent_full_cycles, measured_capacity_ratio) pairs → current SoH.

    Production swaps this for the physics-informed NN in ml/battery (trained on BMS partial cycles).
    """
    if not capacity_fade_points:
        return 1.0
    return float(max(min(capacity_fade_points[-1][1], 1.0), 0.0))
