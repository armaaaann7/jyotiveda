"""Two-stage stochastic, CVaR-risk-aware Model Predictive Control for one transformer.

Here-and-now (shared by all scenarios — non-anticipative):
    battery charge c_t, discharge d_t, SoC, flexible-load shift-out o_t and shift-in n_t
Recourse (per scenario s):
    grid import g_{s,t} ≤ min(grid availability, DT rating), solar curtailment k_{s,t},
    unserved energy by tier: u0 (T0+T1 protected), u2 (livelihood), u3 (flexible+discretionary)

Energy balance, every s,t:
    g + (solar - k) + d - c + u0 + u2 + u3  =  protected + livelihood + other - o + n

Objective (INR):
    E_s[ Σ_t (VoLL0·u0 + VoLL2·u2 + VoLL3·u3 + tariff·g + ε·k)·Δt ]
  + Σ_t (deg·(c+d) + flex_cost·o)·Δt  - terminal SoC value
  + β · CVaR_α( Σ_t u0_{s,t}·Δt )                          (Rockafellar–Uryasev linearisation)

Being an LP it solves in well under a second for 96 slots × 7 scenarios with CLARABEL/HiGHS,
returns *shadow prices* (dual values) used by the explainability layer, and gives the safety
shield a physically consistent plan to validate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np
import structlog

from jyotiveda.observability import MPC_SOLVE_SECONDS

log = structlog.get_logger(__name__)

VOLL_PROTECTED = 500.0  # INR/kWh — life-critical + lifeline
VOLL_LIVELIHOOD = 120.0
VOLL_OTHER = 20.0


@dataclass
class MPCInputs:
    dt_h: float
    protected_kw: np.ndarray  # (S,T) T0+T1 demand
    livelihood_kw: np.ndarray  # (S,T)
    other_kw: np.ndarray  # (S,T) T3+T4
    flexible_kw: np.ndarray  # (T,) shiftable part of T3 (from market + opt-in auto shift)
    solar_kw: np.ndarray  # (S,T)
    grid_cap_kw: np.ndarray  # (S,T) upstream availability (0 during load-shedding)
    tariff_inr: np.ndarray  # (T,)
    probs: np.ndarray  # (S,)
    dt_rating_kw: float
    battery_capacity_kwh: float = 0.0
    soc0: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 0.95
    p_charge_kw: float = 0.0
    p_discharge_kw: float = 0.0
    eta_c: float = 0.96
    eta_d: float = 0.96
    degradation_inr_per_kwh: float = 1.8
    flex_cost_inr_per_kwh: float = 4.0
    shift_in_max_kw: np.ndarray | None = None  # (T,) where rebound may land
    fairness_multiplier: float = 1.0
    terminal_soc_value_inr_per_kwh: float = 6.0
    cvar_alpha: float = 0.9
    cvar_weight: float = 1.0


@dataclass
class MPCPlan:
    status: str
    solve_s: float
    objective_inr: float
    battery_kw: np.ndarray  # + discharge / - charge
    soc: np.ndarray
    shift_out_kw: np.ndarray
    shift_in_kw: np.ndarray
    expected_grid_kw: np.ndarray
    expected_unserved_protected_kw: np.ndarray
    expected_unserved_livelihood_kw: np.ndarray
    expected_unserved_other_kw: np.ndarray
    cvar_protected_kwh: float
    shadow_price_energy_inr: np.ndarray  # marginal value of 1 kWh delivered at t
    shadow_price_soc_inr: np.ndarray  # marginal value of stored energy
    meta: dict = field(default_factory=dict)

    def first_action(self) -> dict:
        return {
            "battery_kw": float(self.battery_kw[0]),
            "shift_out_kw": float(self.shift_out_kw[0]),
            "shift_in_kw": float(self.shift_in_kw[0]),
        }

    def to_dict(self, index=None) -> dict:
        rows = []
        for t in range(len(self.battery_kw)):
            rows.append(
                {
                    "t": index[t].isoformat() if index is not None else t,
                    "battery_kw": round(float(self.battery_kw[t]), 2),
                    "soc": round(float(self.soc[t + 1]), 4),
                    "shift_out_kw": round(float(self.shift_out_kw[t]), 2),
                    "shift_in_kw": round(float(self.shift_in_kw[t]), 2),
                    "grid_kw": round(float(self.expected_grid_kw[t]), 2),
                    "unserved_protected_kw": round(float(self.expected_unserved_protected_kw[t]), 3),
                    "unserved_other_kw": round(float(self.expected_unserved_other_kw[t]), 2),
                    "shadow_price_inr_per_kwh": round(float(self.shadow_price_energy_inr[t]), 2),
                }
            )
        return {
            "status": self.status,
            "solve_s": round(self.solve_s, 3),
            "objective_inr": round(self.objective_inr, 1),
            "cvar_protected_kwh": round(self.cvar_protected_kwh, 3),
            "schedule": rows,
            **self.meta,
        }


def solve_mpc(x: MPCInputs, solver: str = "CLARABEL") -> MPCPlan:
    S, T = x.protected_kw.shape
    dt = x.dt_h
    E = max(x.battery_capacity_kwh, 1e-6)
    has_batt = x.battery_capacity_kwh > 0

    c = cp.Variable(T, nonneg=True)
    d = cp.Variable(T, nonneg=True)
    soc = cp.Variable(T + 1)
    o = cp.Variable(T, nonneg=True)
    n = cp.Variable(T, nonneg=True)
    g = cp.Variable((S, T), nonneg=True)
    k = cp.Variable((S, T), nonneg=True)
    u0 = cp.Variable((S, T), nonneg=True)
    u2 = cp.Variable((S, T), nonneg=True)
    u3 = cp.Variable((S, T), nonneg=True)
    eta = cp.Variable()
    z = cp.Variable(S, nonneg=True)

    shift_in_max = (
        x.shift_in_max_kw
        if x.shift_in_max_kw is not None
        else np.full(T, float(x.flexible_kw.max(initial=0)))
    )
    cons = [
        soc[0] == x.soc0,
        soc[1:] == soc[:-1] + (x.eta_c * c - d / x.eta_d) * dt / E,
        soc >= (x.soc_min if has_batt else 0),
        soc <= (x.soc_max if has_batt else 1),
        c <= x.p_charge_kw,
        d <= x.p_discharge_kw,
        o <= x.flexible_kw,
        n <= shift_in_max,
        cp.sum(n) == cp.sum(o),  # rebound: shifted energy is consumed later in the horizon
        cp.cumsum(n) <= cp.cumsum(o),  # causality: consume only what was deferred earlier
        k <= x.solar_kw,
        u0 <= x.protected_kw,
        u2 <= x.livelihood_kw,
    ]
    balance = cp.Variable((S, T))  # named for dual extraction
    for s in range(S):
        other_net = x.other_kw[s] - o + n
        cons += [
            u3[s] <= other_net,
            g[s] <= np.minimum(x.grid_cap_kw[s], x.dt_rating_kw),
            balance[s] == g[s] + x.solar_kw[s] - k[s] + d - c + u0[s] + u2[s] + u3[s],
        ]
    bal_cons = [
        balance[s] == x.protected_kw[s] + x.livelihood_kw[s] + x.other_kw[s] - o + n for s in range(S)
    ]
    cons += bal_cons
    loss_protected = cp.sum(u0, axis=1) * dt
    cons += [z >= loss_protected - eta]
    cvar = eta + cp.sum(cp.multiply(x.probs, z)) / (1 - x.cvar_alpha)

    exp_cost = 0
    for s in range(S):
        exp_cost += (
            x.probs[s]
            * dt
            * (
                VOLL_PROTECTED * cp.sum(u0[s])
                + VOLL_LIVELIHOOD * cp.sum(u2[s])
                + VOLL_OTHER * cp.sum(u3[s])
                + x.tariff_inr @ g[s]
                + 0.05 * cp.sum(k[s])
            )
        )
    first_stage = dt * (
        x.degradation_inr_per_kwh * cp.sum(c + d)
        + x.flex_cost_inr_per_kwh * x.fairness_multiplier * cp.sum(o)
        + 0.01 * cp.sum(n)
    ) - x.terminal_soc_value_inr_per_kwh * E * soc[T] * (1 if has_batt else 0)
    objective = cp.Minimize(exp_cost + first_stage + x.cvar_weight * VOLL_PROTECTED * cvar)
    prob = cp.Problem(objective, cons)

    t0 = time.perf_counter()
    try:
        prob.solve(solver=solver)
    except Exception:  # solver fallback chain
        log.warning("mpc_primary_solver_failed", solver=solver)
        prob.solve(solver="HIGHS" if solver != "HIGHS" else "CLARABEL")
    solve_s = time.perf_counter() - t0
    status = prob.status
    MPC_SOLVE_SECONDS.labels(status).observe(solve_s)
    if status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"MPC infeasible: {status}")

    p = x.probs[:, None]
    energy_price = np.zeros(T)
    for bc in bal_cons:
        if bc.dual_value is not None:
            energy_price += np.asarray(bc.dual_value).reshape(-1) / dt
    soc_price = (
        np.asarray(cons[1].dual_value).reshape(-1) / E
        if has_batt and cons[1].dual_value is not None
        else np.zeros(T)
    )
    return MPCPlan(
        status=status,
        solve_s=solve_s,
        objective_inr=float(prob.value),
        battery_kw=np.round(d.value - c.value, 4),
        soc=np.clip(soc.value, 0, 1),
        shift_out_kw=np.maximum(o.value, 0),
        shift_in_kw=np.maximum(n.value, 0),
        expected_grid_kw=(p * g.value).sum(0),
        expected_unserved_protected_kw=(p * u0.value).sum(0),
        expected_unserved_livelihood_kw=(p * u2.value).sum(0),
        expected_unserved_other_kw=(p * u3.value).sum(0),
        cvar_protected_kwh=float(cvar.value),
        shadow_price_energy_inr=np.abs(energy_price),
        shadow_price_soc_inr=np.abs(soc_price),
        meta={"scenarios": S, "horizon_slots": T, "solver": solver},
    )


def scenario_set(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic scenario z-scores (Gauss–Hermite-like spread) and equal probabilities."""
    from scipy.stats import norm

    qs = (np.arange(n) + 0.5) / n
    return norm.ppf(qs), np.full(n, 1.0 / n)
