"""digital-twin-service core: closed-loop counterfactual simulation of one transformer neighbourhood.

Runs the *same* physical day twice:

  baseline   — today's practice: no storage, no flexibility, DISCOM rotational load-shedding
  jyotiveda  — Sense → Predict (forecast + gap) → Reliability Budget → Flexibility Market →
               stochastic CVaR-MPC (re-planned every N slots) → Safety Shield → execute →
               fairness-aware lifeline-mode curtailment → P2P settlement → Fairness Debt update

and measures both against the same KPIs (Section 9 of the PRD), with AC power flow per slot.
The planner only sees *forecasts* (from the real forecasting pipeline trained on 14 days of history);
the physics sees the *actual* day — so the improvement is earned, not assumed.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import structlog

from jyotiveda.battery.model import envelope, step_soc
from jyotiveda.config import Settings, get_settings
from jyotiveda.domain import BatteryState, Neighbourhood
from jyotiveda.fairness.debt import FairnessLedger, gini, jain_index
from jyotiveda.flexibility.market import FlexAsset, FlexOffer, clear_market
from jyotiveda.forecasting.base import QuantileForecast
from jyotiveda.forecasting.service import ForecastService, GapForecast
from jyotiveda.optimization.mpc import MPCInputs, MPCPlan, scenario_set, solve_mpc
from jyotiveda.reliability.budget import ReliabilityBudget, build_budget
from jyotiveda.safety.shield import GridState, ProposedAction, SafetyLimits, SafetyShield
from jyotiveda.twin.curtailment import rotational_shed, water_fill
from jyotiveda.twin.neighbourhood import build_neighbourhood
from jyotiveda.twin.profiles import (
    TieredDemand,
    clear_sky_pv,
    cloud_attenuation,
    household_demand,
    slot_index,
    solar_matrix,
    tod_tariff,
)
from jyotiveda.twin.scenario import ScenarioSpec

log = structlog.get_logger(__name__)
P2P_PRICE_INR = 5.2  # between net-metering feed-in (~3) and retail (~6.5-7.8)
EPS = 1e-6


@dataclass
class RunTrace:
    served: np.ndarray  # (T,H)
    demand: np.ndarray  # (T,H) after shifting
    protected: np.ndarray  # (T,H)
    dark: np.ndarray  # (T,H) bool
    curtailed: np.ndarray  # (T,H) kW
    battery_kw: np.ndarray  # (T,)
    soc: np.ndarray  # (T+1,)
    grid_kw: np.ndarray
    solar_used_kw: np.ndarray
    v_min: np.ndarray
    v_max: np.ndarray
    loading_pct: np.ndarray
    shift_out_kw: np.ndarray
    shift_in_kw: np.ndarray
    shield_modified: int = 0
    shield_violations: list[dict] = field(default_factory=list)
    p2p_kwh: float = 0.0
    p2p_value_inr: float = 0.0


@dataclass
class SimulationResult:
    id: str
    spec: ScenarioSpec
    created_at: datetime
    runtime_s: float
    baseline: dict
    jyotiveda: dict
    improvement: dict
    budget: dict
    market: dict
    gap: dict
    plan: dict
    fairness: list[dict]
    timeline: list[dict]
    events: list[dict]
    explanation: dict
    forecast: dict
    topology_summary: dict

    def to_dict(self, include_series: bool = True) -> dict:
        d = {
            "id": self.id,
            "scenario": self.spec.model_dump(mode="json"),
            "created_at": self.created_at.isoformat(),
            "runtime_s": round(self.runtime_s, 2),
            "kpis": {"baseline": self.baseline, "jyotiveda": self.jyotiveda},
            "improvement": self.improvement,
            "reliability_budget": self.budget,
            "flexibility_market": self.market,
            "reliability_gap": self.gap,
            "events": self.events,
            "explanation": self.explanation,
            "fairness_debt": self.fairness,
            "forecast": self.forecast,
            "topology": self.topology_summary,
        }
        if include_series:
            d["timeline"] = self.timeline
            d["plan"] = self.plan
        return d


class TwinSimulator:
    def __init__(
        self, spec: ScenarioSpec, settings: Settings | None = None, nb: Neighbourhood | None = None
    ) -> None:
        self.spec = spec
        self.settings = settings or get_settings()
        self.nb = nb or build_neighbourhood(
            spec.transformer_id,
            n_connections=spec.n_connections,
            battery_kwh=spec.battery_kwh,
            battery_kw=spec.battery_kw,
        )
        self.dt_h = self.settings.slot_minutes / 60
        self.T = int(24 / self.dt_h)
        start = datetime.combine(spec.day, datetime.min.time())
        self.index = slot_index(start, self.T, self.settings.slot_minutes)
        self.hours = self.index.hour.to_numpy() + self.index.minute.to_numpy() / 60
        hh = self.nb.households
        self.H = len(hh)
        self.laterals = np.array([h.lateral for h in hh])
        self.n_laterals = int(self.laterals.max()) + 1
        tr = self.nb.transformer
        # ---- actual physical day ---------------------------------------------------------------
        self.demand = household_demand(hh, self.index, seed=spec.seed, heat_index=spec.heat_index)
        self.pv_clear = clear_sky_pv(self.index, tr.lat or 18.5, tr.lon or 73.9)
        self.cloud = cloud_attenuation(
            self.index, spec.solar_reduction, spec.cloud_start_hour, spec.cloud_end_hour, seed=spec.seed
        )
        self.solar = solar_matrix(hh, self.pv_clear * self.cloud)
        self.grid_cap = self._grid_cap(self.cloud)
        self.tariff = tod_tariff(self.index)
        self.scarcity = self._scarcity_mask()
        self.limits = SafetyLimits(
            soc_min=self.nb.battery.soc_min if self.nb.battery else 0.1,
            soc_max=self.nb.battery.soc_max if self.nb.battery else 0.95,
            battery_max_charge_kw=self.nb.battery.max_charge_kw if self.nb.battery else 0.0,
            battery_max_discharge_kw=self.nb.battery.max_discharge_kw if self.nb.battery else 0.0,
            dt_rating_kw=tr.rating_kw,
        )
        self.shield = SafetyShield(self.limits)
        self.lifeline_floor = {h.id: h.lifeline_kw + h.critical_kw for h in hh}

    # ------------------------------------------------------------------ environment ----
    def _grid_cap(self, cloud: np.ndarray) -> np.ndarray:
        cap = np.full(self.T, self.nb.transformer.rating_kw)
        shock = (1 - cloud) * self.spec.regional_coupling  # regional RE shortfall => feeder curtailment
        cap = cap * (1 - shock)
        for w in self.spec.supply_windows:
            m = (self.hours >= w.start_hour) & (self.hours < w.end_hour)
            cap[m] = np.minimum(cap[m], w.cap_kw)
        return cap

    def _scarcity_mask(self) -> np.ndarray:
        total = self.demand.total.sum(1) - self.solar.sum(1)
        return total > self.grid_cap - 1.0

    # ------------------------------------------------------------------ forecasting -----
    def _history(self, days: int = 14) -> tuple[pd.Series, TieredDemand]:
        start = datetime.combine(self.spec.day, datetime.min.time()) - timedelta(days=days)
        idx = slot_index(start, days * self.T, self.settings.slot_minutes)
        d = household_demand(self.nb.households, idx, seed=self.spec.seed + 1000, heat_index=1.0)
        return pd.Series(d.total.sum(1), index=idx, name="demand_kw"), d

    def _forecasts(self) -> tuple[QuantileForecast, QuantileForecast, dict[str, np.ndarray], dict]:
        svc = ForecastService(self.settings)
        hist, hist_tiers = self._history()
        fc = svc.forecast(hist, self.T)
        fc = QuantileForecast(self.index, fc.q, fc.backend, "demand_kw")
        # tier shares per slot-of-day from history (what share of demand is protected / flexible)
        agg = hist_tiers.aggregate()
        tot = np.maximum(hist_tiers.total.sum(1), EPS)
        days = len(tot) // self.T
        shares = {k: (v / tot).reshape(days, self.T).mean(0) for k, v in agg.items()}
        # weather-AI cloud forecast: smoothed truth + persistent error
        rng = np.random.default_rng(self.spec.seed + 7)
        smooth = pd.Series(self.cloud).rolling(6, center=True, min_periods=1).mean().to_numpy()
        cloud_fc = np.clip(smooth * (1 + rng.normal(0, self.spec.forecast_error)), 0, 1)
        clear_kw = self.pv_clear * self.nb.solar_kwp
        solar_fc = svc.solar(self.index, clear_kw, cloud_fc, 0.08 + self.spec.forecast_error)
        actual = self.demand.total.sum(1)
        meta = {
            "demand_backend": fc.backend,
            "solar_backend": solar_fc.backend,
            "demand_nmae": round(float(np.mean(np.abs(actual - fc.p50)) / np.mean(actual)), 4),
            "demand_coverage_80": round(float(np.mean((actual >= fc.p10) & (actual <= fc.p90))), 3),
            "solar_nmae_daylight": round(
                float(
                    np.abs(self.solar.sum(1) - solar_fc.p50)[clear_kw > 1].mean()
                    / max(self.solar.sum(1)[clear_kw > 1].mean(), EPS)
                ),
                4,
            ),
        }
        return fc, solar_fc, shares, meta

    def _grid_cap_forecast(self, solar_fc: QuantileForecast) -> np.ndarray:
        clear_kw = self.pv_clear * self.nb.solar_kwp
        cloud_fc = np.where(clear_kw > 1, solar_fc.p50 / np.maximum(clear_kw, EPS), 1.0)
        return self._grid_cap(np.clip(cloud_fc, 0, 1))

    # ------------------------------------------------------------------ baseline ---------
    def run_baseline(self, fm=None) -> RunTrace:
        T, H = self.T, self.H
        d = self.demand
        tot = d.total
        served = np.zeros((T, H))
        dark = np.zeros((T, H), dtype=bool)
        grid = np.zeros(T)
        vmin, vmax, load = np.ones(T), np.ones(T), np.zeros(T)
        solar_used = np.zeros(T)
        for t in range(T):
            net_home = tot[t] - self.solar[t]
            deficit = net_home.sum() - self.grid_cap[t]
            dmask = rotational_shed(np.maximum(net_home, 0), self.laterals, deficit, t, self.n_laterals)
            dark[t] = dmask & (tot[t] > EPS)
            served[t] = np.where(dmask, 0.0, tot[t])
            sol = np.where(dmask, 0.0, self.solar[t])
            imp = served[t].sum() - sol.sum()
            if imp < 0:  # reverse-power limit: curtail excess rooftop solar
                sol = sol * (served[t].sum() / max(sol.sum(), EPS))
                imp = 0.0
            grid[t], solar_used[t] = imp, sol.sum()
            if fm is not None:
                r = fm.solve(served[t], sol, 0.0)
                vmin[t], vmax[t], load[t] = r.v_min_pu, r.v_max_pu, r.trafo_loading_pct
            else:
                load[t] = imp / self.nb.transformer.rating_kw * 100
        zeros = np.zeros(T)
        return RunTrace(
            served,
            tot.copy(),
            d.protected.copy(),
            dark,
            np.where(dark, tot, 0.0),
            zeros,
            np.zeros(T + 1),
            grid,
            solar_used,
            vmin,
            vmax,
            load,
            zeros,
            zeros,
        )

    # ------------------------------------------------------------------ jyotiveda --------
    def _flex_offers(self, window: slice, debt: dict[str, float]) -> list[FlexOffer]:
        rng = np.random.default_rng(self.spec.seed + 3)
        offers: list[FlexOffer] = []
        start = self.index[window.start].to_pydatetime()
        end = (self.index[window.stop - 1] + pd.Timedelta(minutes=self.settings.slot_minutes)).to_pydatetime()
        e3 = self.demand.t3[window].sum(0) * self.dt_h
        for j, h in enumerate(self.nb.households):
            if e3[j] < 0.2 or rng.random() > self.spec.flex_participation:
                continue
            asset = (
                FlexAsset.EV
                if h.has_ev
                else rng.choice([FlexAsset.PUMP, FlexAsset.WASHING, FlexAsset.WATER_HEATER])
            )
            energy = float(min(e3[j] * 0.8, 20))
            offers.append(
                FlexOffer(
                    household_id=h.id,
                    transformer_id=self.nb.transformer.id,
                    asset=asset,
                    energy_kwh=round(energy, 3),
                    max_kw=round(max(float(self.demand.t3[window, j].max()), 0.1), 3),
                    window_start=start,
                    window_end=end,
                    min_incentive_inr=round(energy * float(rng.uniform(2.0, 9.0)), 2),
                    divisible=asset == FlexAsset.PUMP,
                )
            )
        return offers

    def run_jyotiveda(self, fm=None) -> tuple[RunTrace, dict]:
        T, H, dt = self.T, self.H, self.dt_h
        s = self.spec
        b = self.nb.battery
        ledger = FairnessLedger.for_households(self.nb.households)
        # prior week's burden (so Fairness Debt is visible from the first event)
        prior = np.random.default_rng(s.seed + 5).gamma(0.6, 0.8, H) * (
            np.random.default_rng(s.seed + 6).random(H) < 0.25
        )
        ledger.debt = prior
        # ---- PREDICT ------------------------------------------------------------------------
        dem_fc, sol_fc, shares, fc_meta = self._forecasts()
        cap_fc = self._grid_cap_forecast(sol_fc)
        gap: GapForecast = ForecastService.gap(dem_fc, sol_fc, cap_fc, seed=s.seed)
        # ---- RELIABILITY BUDGET for the main scarcity window --------------------------------
        risky = np.nonzero(gap.p_shortage >= 0.3)[0]
        if risky.size:
            w = slice(int(risky[0]), int(risky[-1]) + 1)
        else:
            w = slice(int(np.argmax(dem_fc.p50)), int(np.argmax(dem_fc.p50)) + 1)
        soc = s.soc0
        env = envelope(b, BatteryState(battery_id=b.id, ts=datetime.now(UTC), soc=soc)) if b else None
        prot_fc = dem_fc.p50 * (shares["t0"] + shares["t1"])
        solar_surplus_before = np.maximum(sol_fc.p50 - dem_fc.p50, 0)[: w.start].sum() * dt
        offers = self._flex_offers(
            w, {hid: float(ledger.debt[i]) for i, hid in enumerate(ledger.household_ids)}
        )
        p2p_kwh = min(solar_surplus_before, env.headroom_kwh if env else 0.0) if b else 0.0
        budget: ReliabilityBudget = build_budget(
            transformer=self.nb.transformer.id,
            window_start=self.index[w.start].to_pydatetime(),
            window_end=(
                self.index[w.stop - 1] + pd.Timedelta(minutes=self.settings.slot_minutes)
            ).to_pydatetime(),
            shortage_probability=float(gap.p_shortage[w].max()),
            expected_gap_kwh=float(gap.expected_shortage_kw[w].sum() * dt),
            p90_gap_kwh=float(gap.p90_shortage_kw[w].sum() * dt),
            critical_kwh=float(prot_fc[w].sum() * dt),
            battery=env,
            battery_window_kwh_limit=(b.max_discharge_kw * (w.stop - w.start) * dt) if b else 0.0,
            p2p_kwh=float(p2p_kwh),
            flex_offered_kwh=float(sum(o.energy_kwh for o in offers)),
            flex_price_inr=float(np.median([o.price_per_kwh for o in offers])) if offers else 6.0,
            auto_shift_kwh=float(dem_fc.p50[w].sum() * shares["t3"][w].mean() * dt * 0.3),
            discretionary_kwh=float(dem_fc.p50[w].sum() * shares["t4"][w].mean() * dt),
        )
        # ---- FLEXIBILITY MARKET: clear the budget's market slice ----------------------------
        market_need = sum(a.energy_kwh for a in budget.allocation if a.resource == "flexibility_market")
        debt_map = {hid: float(ledger.debt[i]) for i, hid in enumerate(ledger.household_ids)}
        market = clear_market(offers, market_need, debt_map)
        accepted_homes = {a["household_id"] for a in market.accepted}
        acc_mask = np.array([h.id in accepted_homes for h in self.nb.households])
        flex_kw = np.zeros(T)
        wl = w.stop - w.start
        flex_kw[w] += min(
            market.cleared_kwh / (wl * dt),
            float(self.demand.t3[w][:, acc_mask].sum(1).mean()) if acc_mask.any() else 0.0,
        )
        flex_kw += dem_fc.p50 * shares["t3"] * 0.3  # opt-in automatic scheduling
        shift_in_max = np.where(self.scarcity | (gap.p_shortage >= 0.3), 0.0, 60.0)
        # ---- CLOSED LOOP: plan → shield → execute ---------------------------------------------
        z, probs = scenario_set(self.settings.mpc_scenarios)
        tot = self.demand
        owed = np.zeros(H)
        served = np.zeros((T, H))
        demand_after = np.zeros((T, H))
        dark = np.zeros((T, H), dtype=bool)
        curtailed = np.zeros((T, H))
        batt_kw, socs = np.zeros(T), np.zeros(T + 1)
        socs[0] = soc
        grid = np.zeros(T)
        solar_used = np.zeros(T)
        so, si = np.zeros(T), np.zeros(T)
        vmin, vmax, load = np.ones(T), np.ones(T), np.zeros(T)
        plan: MPCPlan | None = None
        plan_t0 = 0
        first_plan: MPCPlan | None = None
        bias = 1.0
        mods, violations = 0, []
        p2p_kwh_total, p2p_val = 0.0, 0.0
        rolling_err: list[float] = []
        for t in range(T):
            if plan is None or (t - plan_t0) >= s.replan_every_slots:
                rem = slice(t, T)
                Tn = T - t
                dmed = dem_fc.p50[rem] * bias
                dscen = np.stack([dem_fc.sample(zz)[rem] * bias for zz in z])
                sscen = np.stack([sol_fc.sample(-zz)[rem] for zz in z])
                capscen = np.tile(cap_fc[rem], (len(z), 1))
                x = MPCInputs(
                    dt_h=dt,
                    protected_kw=dscen * (shares["t0"] + shares["t1"])[rem],
                    livelihood_kw=dscen * shares["t2"][rem],
                    other_kw=dscen * (shares["t3"] + shares["t4"])[rem],
                    flexible_kw=np.minimum(flex_kw[rem], dmed * shares["t3"][rem]),
                    solar_kw=sscen,
                    grid_cap_kw=capscen,
                    tariff_inr=self.tariff[rem],
                    probs=probs,
                    dt_rating_kw=self.nb.transformer.rating_kw,
                    battery_capacity_kwh=b.capacity_kwh if b else 0.0,
                    soc0=soc,
                    soc_min=b.soc_min if b else 0.0,
                    soc_max=b.soc_max if b else 1.0,
                    p_charge_kw=b.max_charge_kw if b else 0.0,
                    p_discharge_kw=b.max_discharge_kw if b else 0.0,
                    degradation_inr_per_kwh=env.degradation_cost_inr_per_kwh if env else 0.0,
                    shift_in_max_kw=shift_in_max[rem] if Tn > 1 else None,
                    cvar_alpha=self.settings.mpc_cvar_alpha,
                    terminal_soc_value_inr_per_kwh=4.0,
                )
                try:
                    plan = solve_mpc(x, self.settings.mpc_solver)
                except Exception:
                    log.exception("mpc_failed_using_rule_fallback")
                    plan = None
                plan_t0 = t
                if first_plan is None and plan is not None:
                    first_plan = plan
            k = t - plan_t0
            # --- per-home actual demand and flexible-load shifting ----------------------------
            d0, d1, d2, d3, d4 = (getattr(tot, n)[t].copy() for n in ("t0", "t1", "t2", "t3", "t4"))
            want_out = float(plan.shift_out_kw[k]) if plan is not None else 0.0
            w_out = ledger.weights() * np.where(acc_mask, 3.0, 1.0)
            take = water_fill(d3, w_out, want_out)
            d3 -= take
            want_in = float(plan.shift_in_kw[k]) if plan is not None else 0.0
            give = water_fill(owed.copy(), np.ones(H), min(want_in, owed.sum()))
            d3 += give
            owed += take * 1.0 - give
            so[t], si[t] = take.sum(), give.sum()
            dem = d0 + d1 + d2 + d3 + d4
            demand_after[t] = dem
            sol = self.solar[t]
            net = dem.sum() - sol.sum()
            cap = self.grid_cap[t]
            # --- battery setpoint: plan + real-time correction (edge controller) --------------
            bset = float(plan.battery_kw[k]) if plan is not None else 0.0
            if net - bset > cap:
                bset = net - cap
            if net - bset < 0 and cap > 0:
                bset = min(bset, net)  # soak surplus solar into storage
            if b:
                gs = GridState(
                    ts=self.index[t].to_pydatetime().replace(tzinfo=UTC),
                    battery_soc=soc,
                    battery_soh=1.0,
                    battery_capacity_kwh=b.capacity_kwh,
                    battery_temp_c=31.0,
                    battery_power_kw=float(batt_kw[t - 1]) if t else 0.0,
                    battery_alarms=(),
                    dt_load_kw=net,
                    v_min_pu=float(vmin[t - 1]) if t else 1.0,
                    v_max_pu=float(vmax[t - 1]) if t else 1.0,
                    telemetry_age_s=5.0,
                    lifeline_kw_by_household=self.lifeline_floor,
                )
                issued = gs.ts
                dec = self.shield.validate(
                    ProposedAction(
                        action_id=f"sim-{t}",
                        issued_at=issued,
                        valid_for_s=900,
                        battery_kw=bset,
                        duration_s=dt * 3600,
                        source="mpc+edge",
                    ),
                    gs,
                    now=issued,
                )
                if dec.verdict.value == "MODIFIED":
                    mods += 1
                    violations += [{**v, "slot": self.index[t].isoformat()} for v in dec.violations]
                bset = dec.action.battery_kw if dec.action else 0.0
            else:
                bset = 0.0
            imp = net - bset
            curt = np.zeros(H)
            wts = ledger.weights()
            if imp > cap:  # lifeline mode: curtail T4 → T3 → T2, never T1/T0
                deficit = imp - cap
                for tier in (d4, d3, d2):
                    if deficit <= EPS:
                        break
                    c = water_fill(tier.copy(), wts, deficit)
                    tier -= c
                    curt += c
                    deficit -= c.sum()
                if deficit > EPS:  # cannot even serve lifeline: shed whole homes, lowest-debt first
                    order = np.argsort(ledger.debt + np.arange(H) * 1e-9)
                    for j in order:
                        if deficit <= EPS:
                            break
                        home_net = d0[j] + d1[j] + d2[j] + d3[j] + d4[j] - sol[j]
                        if home_net <= 0:
                            continue
                        dark[t, j] = True
                        deficit -= home_net
                        curt[j] += d0[j] + d1[j] + d2[j] + d3[j] + d4[j]
                        d0[j] = d1[j] = d2[j] = d3[j] = d4[j] = 0.0
                imp = cap
            srv = d0 + d1 + d2 + d3 + d4
            served[t] = srv
            curtailed[t] = curt
            sol_eff = np.where(dark[t], 0.0, sol)
            imp = srv.sum() - sol_eff.sum() - bset
            if imp < 0:
                sol_eff = sol_eff * max((srv.sum() - bset), 0) / max(sol_eff.sum(), EPS)
                imp = 0.0
            grid[t], solar_used[t] = imp, sol_eff.sum()
            if b:
                soc = step_soc(b, soc, 1.0, bset, dt)
            batt_kw[t], socs[t + 1] = bset, soc
            # --- P2P settlement inside the DT --------------------------------------------------
            surplus = np.maximum(sol_eff - srv, 0).sum()
            deficit_homes = np.maximum(srv - sol_eff, 0).sum()
            traded = min(surplus, deficit_homes) * dt
            p2p_kwh_total += traded
            p2p_val += traded * P2P_PRICE_INR
            # --- fairness + online forecast bias correction ------------------------------------
            ledger.update(take * dt, curt * dt, tot.total[t] * dt)
            rolling_err.append(tot.total[t].sum() / max(dem_fc.p50[t], EPS))
            bias = float(np.clip(np.mean(rolling_err[-4:]), 0.8, 1.25))
            if fm is not None:
                r = fm.solve(srv, sol_eff, bset)
                vmin[t], vmax[t], load[t] = r.v_min_pu, r.v_max_pu, r.trafo_loading_pct
            else:
                load[t] = imp / self.nb.transformer.rating_kw * 100
        trace = RunTrace(
            served,
            demand_after,
            tot.protected.copy(),
            dark,
            curtailed,
            batt_kw,
            socs,
            grid,
            solar_used,
            vmin,
            vmax,
            load,
            so,
            si,
            mods,
            violations[:50],
            p2p_kwh_total,
            p2p_val,
        )
        ctx = {
            "budget": budget,
            "market": market,
            "gap": gap,
            "plan": first_plan,
            "ledger": ledger,
            "forecast_meta": fc_meta,
            "demand_fc": dem_fc,
            "solar_fc": sol_fc,
            "window": w,
            "offers": len(offers),
        }
        return trace, ctx

    # ------------------------------------------------------------------ KPIs --------------
    def kpis(self, tr: RunTrace) -> dict:
        dt = self.dt_h
        prot_short = tr.served < (tr.protected - 1e-6)
        demand_pos = tr.demand > EPS
        win = self.scarcity
        served_ratio = (
            tr.served[win].sum(0) / np.maximum(tr.demand[win].sum(0), EPS) if win.any() else np.ones(self.H)
        )
        ens = float(np.maximum(tr.demand - tr.served, 0).sum() * dt)
        viol = int(((tr.v_min < 0.94) | (tr.v_max > 1.06)).sum())
        return {
            "critical_outage_home_hours": round(float(prot_short.sum() * dt), 2),
            "critical_outage_hours_per_household": round(float(prot_short.sum() * dt / self.H), 3),
            "household_outage_home_hours": round(float((tr.dark & demand_pos).sum() * dt), 2),
            "household_outage_hours_per_household": round(
                float((tr.dark & demand_pos).sum() * dt / self.H), 3
            ),
            "homes_with_uninterrupted_lifeline": int((~prot_short.any(0)).sum()),
            "lifeline_availability_in_scarcity": round(
                float(1 - prot_short[win].mean()) if win.any() else 1.0, 4
            ),
            "energy_not_served_kwh": round(ens, 2),
            "energy_shifted_kwh": round(float(tr.shift_out_kw.sum() * dt), 2),
            "dt_peak_import_kw": round(float(tr.grid_kw.max()), 2),
            "dt_peak_loading_pct": round(float(tr.loading_pct.max()), 1),
            "voltage_violation_slots": viol,
            "v_min_pu": round(float(tr.v_min.min()), 4),
            "fairness_gini_served_ratio": round(gini(served_ratio), 4),
            "fairness_jain_index": round(jain_index(served_ratio), 4),
            "battery_throughput_kwh": round(float(np.abs(tr.battery_kw).sum() * dt), 2),
            "battery_final_soc": round(float(tr.soc[-1]), 3),
            "solar_used_kwh": round(float(tr.solar_used_kw.sum() * dt), 2),
            "p2p_traded_kwh": round(tr.p2p_kwh, 2),
            "p2p_value_inr": round(tr.p2p_value_inr, 2),
            "shield_modified_commands": tr.shield_modified,
        }

    @staticmethod
    def improvement(base: dict, jv: dict) -> dict:
        def red(k: str) -> float | None:
            return round((1 - jv[k] / base[k]) * 100, 1) if base[k] > 0 else None

        return {
            "critical_outage_reduction_pct": red("critical_outage_home_hours"),
            "household_outage_reduction_pct": red("household_outage_home_hours"),
            "energy_not_served_reduction_pct": red("energy_not_served_kwh"),
            "lifeline_availability_gain_pts": round(
                (jv["lifeline_availability_in_scarcity"] - base["lifeline_availability_in_scarcity"]) * 100, 2
            ),
            "additional_homes_with_uninterrupted_lifeline": jv["homes_with_uninterrupted_lifeline"]
            - base["homes_with_uninterrupted_lifeline"],
            "voltage_violation_reduction": base["voltage_violation_slots"] - jv["voltage_violation_slots"],
            "gini_change": round(jv["fairness_gini_served_ratio"] - base["fairness_gini_served_ratio"], 4),
        }

    # ------------------------------------------------------------------ run ----------------
    def run(self) -> SimulationResult:
        t0 = time.perf_counter()
        fm = None
        if self.spec.run_power_flow:
            from jyotiveda.twin.network import FeederModel

            fm = FeederModel(self.nb)
        base = self.run_baseline(fm)
        jv, ctx = self.run_jyotiveda(fm)
        kb, kj = self.kpis(base), self.kpis(jv)
        from jyotiveda.explain.attribution import event_timeline, explain_peak_decision

        timeline = []
        for t in range(self.T):
            timeline.append(
                {
                    "ts": self.index[t].isoformat(),
                    "demand_kw": round(float(self.demand.total[t].sum()), 2),
                    "solar_kw": round(float(self.solar[t].sum()), 2),
                    "grid_cap_kw": round(float(self.grid_cap[t]), 2),
                    "tariff_inr": float(self.tariff[t]),
                    "baseline": {
                        "served_kw": round(float(base.served[t].sum()), 2),
                        "homes_dark": int(base.dark[t].sum()),
                        "homes_lifeline_ok": int((base.served[t] >= base.protected[t] - 1e-6).sum()),
                        "v_min_pu": round(float(base.v_min[t]), 4),
                        "dt_loading_pct": round(float(base.loading_pct[t]), 1),
                    },
                    "jyotiveda": {
                        "served_kw": round(float(jv.served[t].sum()), 2),
                        "battery_kw": round(float(jv.battery_kw[t]), 2),
                        "soc": round(float(jv.soc[t + 1]), 4),
                        "grid_kw": round(float(jv.grid_kw[t]), 2),
                        "shift_out_kw": round(float(jv.shift_out_kw[t]), 2),
                        "shift_in_kw": round(float(jv.shift_in_kw[t]), 2),
                        "curtailed_kw": round(float(jv.curtailed[t].sum()), 2),
                        "homes_dark": int(jv.dark[t].sum()),
                        "homes_in_lifeline_mode": int(((jv.curtailed[t] > 1e-6) & ~jv.dark[t]).sum()),
                        "homes_lifeline_ok": int((jv.served[t] >= jv.protected[t] - 1e-6).sum()),
                        "v_min_pu": round(float(jv.v_min[t]), 4),
                        "dt_loading_pct": round(float(jv.loading_pct[t]), 1),
                    },
                    "gap_probability": round(float(ctx["gap"].p_shortage[t]), 3),
                }
            )
        plan: MPCPlan | None = ctx["plan"]
        res = SimulationResult(
            id=f"SIM-{uuid.uuid4().hex[:12]}",
            spec=self.spec,
            created_at=datetime.now(UTC),
            runtime_s=time.perf_counter() - t0,
            baseline=kb,
            jyotiveda=kj,
            improvement=self.improvement(kb, kj),
            budget=ctx["budget"].to_dict(),
            market=ctx["market"].to_dict() | {"offers_received": ctx["offers"]},
            gap=ctx["gap"].to_dict(),
            plan=plan.to_dict(self.index) if plan else {},
            fairness=sorted(ctx["ledger"].snapshot(), key=lambda r: -r["debt"])[:30],
            timeline=timeline,
            events=event_timeline(self, jv, ctx),
            explanation=explain_peak_decision(self, jv, ctx),
            forecast=ctx["forecast_meta"],
            topology_summary={
                "transformer": self.nb.transformer.model_dump(),
                "households": self.H,
                "rooftop_solar_kwp": self.nb.solar_kwp,
                "battery": self.nb.battery.model_dump() if self.nb.battery else None,
                "laterals": self.n_laterals,
            },
        )
        log.info(
            "simulation_complete", id=res.id, runtime_s=round(res.runtime_s, 2), improvement=res.improvement
        )
        return res
