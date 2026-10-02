"""Gymnasium environment for constrained RL (PPO-Lagrangian) over the aggregate DT twin.

Observation (13): [sin/cos time, SoC, protected kW, other kW, solar kW, grid cap kW,
                   gap P(shortage) next 1h/4h, flex available kW, tariff, MPC battery setpoint, fairness mean]
Action (2, in [-1, 1]): residual on the MPC battery setpoint (± 30% of rating), flex activation share.
Reward: - (VoLL-weighted unserved energy + grid cost + degradation + fairness burden)
Cost (constraint signal, info["cost"]): protected-load shortfall + SoC-limit proximity.

The RL policy learns *residual* corrections to MPC (forecast-error patterns, rebound behaviour)
and is ALWAYS passed through the safety shield; if it's missing or unhealthy, MPC runs alone.
"""

from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from jyotiveda.twin.scenario import ScenarioSpec


class DTFlexEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, spec: ScenarioSpec | None = None, randomize: bool = True) -> None:
        super().__init__()
        self.base_spec = spec or ScenarioSpec(run_power_flow=False)
        self.randomize = randomize
        self.observation_space = spaces.Box(-np.inf, np.inf, shape=(13,), dtype=np.float32)
        self.action_space = spaces.Box(-1.0, 1.0, shape=(2,), dtype=np.float32)
        self._load()

    def _load(self, seed: int | None = None) -> None:
        from jyotiveda.twin.simulator import TwinSimulator

        spec = self.base_spec
        if self.randomize and seed is not None:
            rng = np.random.default_rng(seed)
            spec = spec.model_copy(
                update={
                    "solar_reduction": float(rng.uniform(0, 0.9)),
                    "heat_index": float(rng.uniform(0.9, 1.3)),
                    "seed": int(rng.integers(0, 10_000)),
                    "soc0": float(rng.uniform(0.2, 0.9)),
                }
            )
        sim = TwinSimulator(spec)
        self.sim = sim
        a = sim.demand.aggregate()
        self.protected = a["t0"] + a["t1"]
        self.other = a["t2"] + a["t3"] + a["t4"]
        self.flex = a["t3"]
        self.solar = sim.solar.sum(1)
        self.cap = sim.grid_cap
        self.tariff = sim.tariff
        self.b = sim.nb.battery
        self.T = sim.T
        self.dt = sim.dt_h

    def _obs(self) -> np.ndarray:
        t = self.t
        h = t / self.T * 2 * np.pi
        short = np.maximum(self.protected + self.other - self.solar - self.cap, 0)
        p1 = float((short[t : t + 4] > 1).mean()) if t < self.T else 0.0
        p4 = float((short[t : t + 16] > 1).mean()) if t < self.T else 0.0
        tt = min(t, self.T - 1)
        return np.array(
            [
                np.sin(h),
                np.cos(h),
                self.soc,
                self.protected[tt] / 100,
                self.other[tt] / 100,
                self.solar[tt] / 100,
                self.cap[tt] / 250,
                p1,
                p4,
                self.flex[tt] / 100,
                self.tariff[tt] / 10,
                self.mpc_hint[tt] / 100,
                0.0,
            ],
            dtype=np.float32,
        )

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self._load(seed)
        self.t = 0
        self.soc = self.sim.spec.soc0
        # heuristic MPC-like hint: discharge into the gap, charge from surplus
        net = self.protected + self.other - self.solar - self.cap
        self.mpc_hint = (
            np.clip(net, -self.b.max_charge_kw, self.b.max_discharge_kw) if self.b else np.zeros(self.T)
        )
        return self._obs(), {}

    def step(self, action: np.ndarray):
        from jyotiveda.battery.model import step_soc

        t = self.t
        a = np.clip(action, -1, 1)
        rating = self.b.max_discharge_kw if self.b else 0.0
        batt = float(np.clip(self.mpc_hint[t] + 0.3 * rating * a[0], -rating, rating))
        cap_kwh = self.b.capacity_kwh
        max_dis = (self.soc - self.b.soc_min) * cap_kwh / self.dt
        max_ch = (self.b.soc_max - self.soc) * cap_kwh / self.dt
        batt = float(np.clip(batt, -max_ch, max_dis))  # shield-equivalent clip during training
        shift = float((a[1] + 1) / 2 * self.flex[t] * 0.5)
        load = self.protected[t] + self.other[t] - shift
        deficit = max(load - self.solar[t] - batt - self.cap[t], 0.0)
        u_other = min(deficit, self.other[t] - shift)
        u_prot = max(deficit - u_other, 0.0)
        grid = max(load - self.solar[t] - batt - deficit, 0.0)
        self.soc = step_soc(self.b, self.soc, 1.0, batt, self.dt)
        reward = (
            -(500 * u_prot + 20 * u_other + self.tariff[t] * grid + 1.8 * abs(batt) + 4 * shift)
            * self.dt
            / 100
        )
        cost = u_prot * self.dt + max(0.12 - self.soc, 0) * 10
        self.t += 1
        done = self.t >= self.T
        return (
            self._obs(),
            float(reward),
            done,
            False,
            {"cost": cost, "unserved_protected_kwh": u_prot * self.dt},
        )
