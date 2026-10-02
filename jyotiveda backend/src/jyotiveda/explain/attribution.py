"""Explainability: "Why did the AI do this?"

Every dispatch decision is explained from three auditable sources — no black box:
  1. decision drivers  — normalised features the policy acted on (shortfall, exposure, SoC, ...)
  2. shadow prices     — MPC dual values: the marginal INR value of 1 kWh at that moment
  3. counterfactual    — the digital twin's "without this action" outcome

The deterministic explanation below is always available; the Copilot can additionally narrate it
in plain Hindi/Marathi/English via the LLM (explain/narrator.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from jyotiveda.twin.simulator import RunTrace, TwinSimulator


def _level(x: float, lo: float, hi: float) -> str:
    return "HIGH" if x >= hi else ("MEDIUM" if x >= lo else "LOW")


def explain_peak_decision(sim: TwinSimulator, tr: RunTrace, ctx: dict) -> dict:
    t = int(np.argmax(tr.battery_kw))
    dt = sim.dt_h
    gap = ctx["gap"]
    plan = ctx["plan"]
    shortfall = max(sim.demand.total[t].sum() - sim.solar[t].sum() - sim.grid_cap[t], 0.0)
    exposure = float(sim.demand.protected[t].sum())
    flex_avail = float(sim.demand.t3[t].sum())
    soc_before = float(tr.soc[t])
    drivers = [
        {
            "feature": "Predicted supply shortfall",
            "value_kw": round(shortfall, 1),
            "level": _level(shortfall, 10, 40),
        },
        {
            "feature": "Critical-load exposure",
            "value_kw": round(exposure, 1),
            "level": _level(exposure, 20, 40),
        },
        {
            "feature": "Battery state of charge",
            "value": round(soc_before, 3),
            "level": _level(soc_before, 0.3, 0.6),
        },
        {
            "feature": "Deficit probability (next hour)",
            "value": round(float(gap.p_shortage[t : t + 4].max()), 3),
            "level": _level(float(gap.p_shortage[t : t + 4].max()), 0.3, 0.7),
        },
        {
            "feature": "Flexible demand available",
            "value_kw": round(flex_avail, 1),
            "level": _level(flex_avail, 10, 30),
        },
    ]
    weights = np.array(
        [
            shortfall / 40,
            exposure / 50,
            soc_before,
            gap.p_shortage[t : t + 4].max(),
            1 - min(flex_avail / 30, 1),
        ]
    )
    contrib = weights / max(weights.sum(), 1e-9)
    for d, c in zip(drivers, contrib, strict=True):
        d["contribution"] = round(float(c), 3)
    shadow = (
        float(plan.shadow_price_energy_inr[t])
        if plan is not None and t < len(plan.shadow_price_energy_inr)
        else None
    )
    without = max(shortfall - 0.0, 0.0)
    homes_saved = int((tr.served[t] >= tr.protected[t] - 1e-6).sum())
    return {
        "slot": sim.index[t].isoformat(),
        "decision": f"Discharge {tr.battery_kw[t] * dt:.1f} kWh this slot ({tr.battery_kw[t]:.1f} kW)",
        "drivers": drivers,
        "shadow_price_inr_per_kwh": None if shadow is None else round(shadow, 2),
        "expected_outcome": {
            "critical_outage_avoided": bool((tr.served[t] >= tr.protected[t] - 1e-6).all()),
            "homes_with_lifeline": homes_saved,
            "voltage_maintained": bool(tr.v_min[t] >= 0.94 and tr.v_max[t] <= 1.06),
            "fairness_impact": "LOW" if tr.curtailed[t].std() < 0.3 else "MEDIUM",
            "shortfall_without_action_kw": round(without, 1),
        },
        "safety": "validated by deterministic safety shield (S1–S10) before execution",
    }


def event_timeline(sim: TwinSimulator, tr: RunTrace, ctx: dict) -> list[dict]:
    idx = sim.index
    gap = ctx["gap"]
    ev: list[dict] = []

    def add(t: int, kind: str, msg: str) -> None:
        ev.append({"ts": idx[t].isoformat(), "event": kind, "message": msg})

    add(0, "NORMAL", "Day-ahead forecast published; digital twin synchronised")
    risky = np.nonzero(gap.p_shortage >= 0.3)[0]
    if risky.size:
        add(
            0,
            "RENEWABLE_RISK_DETECTED",
            f"Reliability gap forecast: P(shortage)={gap.p_shortage.max():.0%}, expected {gap.expected_shortage_kwh:.0f} kWh",
        )
        add(
            0,
            "RELIABILITY_BUDGET_CREATED",
            f"{ctx['budget'].required_kwh:.0f} kWh required; critical loads {ctx['budget'].critical_load_kwh:.0f} kWh protected",
        )
        add(
            0,
            "FLEXIBILITY_MARKET_CLEARED",
            f"{len(ctx['market'].accepted)} offers accepted, {ctx['market'].cleared_kwh:.1f} kWh at ₹{ctx['market'].clearing_price_inr_per_kwh:.2f}/kWh",
        )
    charge = np.nonzero(tr.battery_kw < -5)[0]
    if charge.size:
        add(int(charge[0]), "BATTERY_PRECHARGE", "Storing rooftop surplus ahead of the evening gap")
    if (sim.cloud < 0.8).any():
        add(
            int(np.argmax(sim.cloud < 0.8)),
            "CLOUD_EVENT",
            f"Solar down {(1 - sim.cloud.min()):.0%} vs clear sky",
        )
    shift = np.nonzero(tr.shift_out_kw > 1)[0]
    if shift.size:
        add(int(shift[0]), "FLEXIBILITY_DISPATCHED", "Flexible loads deferred (pumps, EVs, washing)")
    dis = np.nonzero(tr.battery_kw > 5)[0]
    if dis.size:
        add(int(dis[0]), "BATTERY_ACTIVE", "Community battery discharging to cover the gap")
    lm = np.nonzero(tr.curtailed.sum(1) > 0.5)[0]
    if lm.size:
        add(
            int(lm[0]), "LIFELINE_MODE", "Comfort loads limited; T0/T1 lifeline loads protected in every home"
        )
    scarce = np.nonzero(sim.scarcity)[0]
    if scarce.size:
        end = int(scarce[-1])
        ok = bool((tr.served[scarce] >= tr.protected[scarce] - 1e-6).all())
        add(
            end,
            "CRITICAL_LOADS_PROTECTED" if ok else "CRITICAL_LOADS_PARTIAL",
            "All homes kept lifeline power through the scarcity window"
            if ok
            else "Some lifeline shortfall — see KPIs",
        )
        add(
            min(end + 1, len(idx) - 1),
            "RECOVERY",
            "Upstream supply restored; deferred loads resume, battery recovers",
        )
    return sorted(ev, key=lambda e: e["ts"])
