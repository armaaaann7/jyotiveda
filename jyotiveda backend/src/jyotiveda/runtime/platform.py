"""Platform composition root: wires bus, storage, AI services, reliability engine, optimiser,
safety shield, dispatch orchestrator, edge gateways and the control loop.

`run_cycle()` is the production 15-minute loop for one transformer:

  SENSE      live twin/edge state
  PREDICT    demand (Chronos-2/GBM) + solar (clear-sky x weather AI) + Reliability Gap (copula MC)
  UNDERSTAND grid-intelligence risk (GNN or LinDistFlow physics)
  ALLOCATE   Reliability Budget → fairness-aware flexibility market clearing
  OPTIMISE   stochastic CVaR-MPC → RL residual (health-gated)
  PROTECT    safety shield (cloud) → dispatch orchestrator → signed command → edge shield → device
  SHARE      events to Kafka/WebSocket; audit chain; fairness ledger
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime

import numpy as np
import structlog

from jyotiveda.audit.ledger import AuditLedger
from jyotiveda.battery.model import envelope
from jyotiveda.config import Settings
from jyotiveda.dispatch.orchestrator import DispatchOrchestrator
from jyotiveda.domain import BatteryState
from jyotiveda.edge.gateway import InProcessEdgeTransport, plan_from_mpc
from jyotiveda.events import CloudEvent, Topic, build_bus
from jyotiveda.flexibility.market import clear_market
from jyotiveda.gridintel.graph import build_graph
from jyotiveda.gridintel.risk import build_risk_engine
from jyotiveda.observability import CONTROL_CYCLE_SECONDS, CRITICAL_PROTECTED, RELIABILITY_GAP_KWH
from jyotiveda.optimization.mpc import MPCInputs, scenario_set, solve_mpc
from jyotiveda.reliability.budget import build_budget
from jyotiveda.rl.policy import ResidualPolicy
from jyotiveda.runtime.fleet import TransformerRuntime, build_fleet
from jyotiveda.safety.shield import ProposedAction, SafetyShield
from jyotiveda.security.rbac import Principal, Role
from jyotiveda.security.signing import CommandSigner
from jyotiveda.storage.db import Database
from jyotiveda.twin.scenario import ScenarioSpec
from jyotiveda.twin.simulator import TwinSimulator

log = structlog.get_logger(__name__)
SYSTEM = Principal(sub="svc:control-loop", role=Role.SERVICE)


class Platform:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bus = build_bus(settings.event_bus, settings.kafka_bootstrap, settings.kafka_client_id)
        self.db = Database(settings.database_url)
        self.ledger = AuditLedger(sink=self.db.add_audit)
        self.signer = CommandSigner.from_path_or_ephemeral(settings.command_signing_key_path)
        self.fleet: dict[str, TransformerRuntime] = build_fleet(settings, self.signer.public_pem())
        self.transport = InProcessEdgeTransport({k: rt.gateway for k, rt in self.fleet.items()})
        # cloud shield uses the same limits as each DT's edge shield; per-DT shields keyed by id
        self.shields = {k: SafetyShield(rt.limits) for k, rt in self.fleet.items()}
        self.orchestrator = DispatchOrchestrator(
            shield=next(iter(self.shields.values())),
            bus=self.bus,
            ledger=self.ledger,
            signer=self.signer,
            transport=self.transport,
            auto_approve_max_kw=settings.auto_approve_max_kw,
        )
        self.orchestrator.sink = self.db.upsert_dispatch
        self.transport.orchestrator = self.orchestrator
        self.risk_engine = build_risk_engine(settings.gnn_checkpoint)
        self.policy = ResidualPolicy(settings.rl_policy_onnx)
        self.simulations: dict[str, dict] = {}
        self._tasks: list[asyncio.Task] = []
        self.started_at = datetime.now(UTC)

    # ------------------------------------------------------------------ lifecycle -------
    async def start(self) -> None:
        await self.db.init()
        await self.bus.start()
        self._tasks.append(asyncio.create_task(self._live_publisher()))
        self._tasks.append(asyncio.create_task(asyncio.to_thread(self._warm)))
        if self.settings.control_loop_enabled:
            self._tasks.append(asyncio.create_task(self._control_loop()))
        await self.ledger.append(
            "svc:platform",
            "SERVICE",
            "platform.started",
            "platform",
            self.settings.role.value,
            "boot",
            {"transformers": list(self.fleet), "signing_kid": self.signer.key_id},
        )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await self.bus.stop()
        await self.db.close()

    def _warm(self) -> None:
        """Pre-compute day-ahead forecasts for the fleet off the event loop (fast first dashboard load)."""
        for rt in self.fleet.values():
            try:
                rt.forecasts()
            except Exception:  # pragma: no cover
                log.exception("forecast_warmup_failed", transformer=rt.id)

    async def _live_publisher(self, every_s: float = 5.0) -> None:
        while True:
            for rt in self.fleet.values():
                st = rt.live_state()
                rt.gateway.telemetry(st["net_import_kw"], 0.97, 1.01, st["grid_cap_kw"])
                await self.bus.publish(CloudEvent.of(Topic.TRANSFORMER_STATE, "jyotiveda/twin", st, rt.id))
                await rt.gateway.tick()
                rt.gateway.pcs.advance(every_s)  # type: ignore[attr-defined]
            await asyncio.sleep(every_s)

    async def _control_loop(self) -> None:  # pragma: no cover - timing loop
        while True:
            for dt_id in self.fleet:
                try:
                    await self.run_cycle(dt_id, SYSTEM)
                except Exception:
                    log.exception("control_cycle_failed", transformer=dt_id)
            await asyncio.sleep(self.settings.control_loop_interval_s)

    # ------------------------------------------------------------------ intelligence ----
    def risk(self, rt: TransformerRuntime, t: int | None = None) -> dict:
        fc = rt.forecasts()
        s = rt.sim
        t = rt.slot() if t is None else t
        horizon = slice(t, min(t + 16, s.T))
        k = t + int(np.argmax(fc["demand"].p90[horizon]))
        ratio = fc["demand"].p90[k] / max(fc["demand"].p50[k], 1e-6)
        solar_ratio = fc["solar"].p10[k] / max(fc["solar"].p50[k], 1e-6) if fc["solar"].p50[k] > 0 else 0.0
        g = build_graph(s.nb, s.demand.total[k] * ratio, s.solar[k] * solar_ratio, s.demand.protected[k])
        rep = self.risk_engine.score(g, supply_cap_kw=float(fc["cap"][k]))
        return rep.to_dict() | {"assessed_slot": s.index[k].isoformat()}

    def fleet_overview(self) -> list[dict]:
        """Rank transformers by *residual* stress: forecast gap left after the DT's own storage,
        blended with physical overload/voltage risk. Drives the DISCOM 'stress radar'."""
        rows = []
        for rt in self.fleet.values():
            fc = rt.forecasts()
            t = rt.slot()
            gap = fc["gap"]
            r = self.risk(rt, t)
            p90 = float(gap.p90_shortage_kw[t:].sum() * rt.sim.dt_h)
            b = rt.sim.nb.battery
            usable = max(rt.gateway.pcs.soc - (b.soc_min if b else 0), 0) * (b.capacity_kwh if b else 0)  # type: ignore[attr-defined]
            residual = p90 / (p90 + usable + 50.0)
            physical = 1 - (1 - r["overload_risk"]) * (1 - r["voltage_risk"])
            stress = round(0.65 * residual + 0.35 * physical, 3)
            level = (
                "CRITICAL"
                if stress >= 0.7
                else "HIGH"
                if stress >= 0.5
                else "MODERATE"
                if stress >= 0.3
                else "LOW"
            )
            rows.append(
                {
                    **rt.live_state(),
                    "risk": r | {"level": level},
                    "gap_expected_kwh_24h": round(float(gap.expected_shortage_kw[t:].sum() * rt.sim.dt_h), 1),
                    "gap_p90_kwh_24h": round(p90, 1),
                    "battery_usable_kwh": round(usable, 1),
                    "households": rt.sim.H,
                    "rating_kva": rt.sim.nb.transformer.rating_kva,
                    "stress_score": stress,
                }
            )
        return sorted(rows, key=lambda r: -r["stress_score"])

    async def run_cycle(self, dt_id: str, principal: Principal, now: datetime | None = None) -> dict:
        t_start = time.perf_counter()
        rt = self.fleet[dt_id]
        s = rt.sim
        dt = s.dt_h
        fc = rt.forecasts()
        t = rt.slot(now)
        b = s.nb.battery
        pcs = rt.gateway.pcs
        soc = float(pcs.soc)  # type: ignore[attr-defined]
        env = envelope(b, BatteryState(battery_id=b.id, ts=datetime.now(UTC), soc=soc)) if b else None
        gap = fc["gap"]
        dem, sol, shares, cap = fc["demand"], fc["solar"], fc["shares"], fc["cap"]
        await self.bus.publish(
            CloudEvent.of(
                Topic.FORECAST_CREATED,
                "jyotiveda/forecast",
                {"transformer_id": dt_id, **fc["meta"], "horizon_slots": s.T - t},
                dt_id,
            )
        )
        risk = self.risk(rt, t)
        await self.bus.publish(
            CloudEvent.of(
                Topic.RISK_UPDATED, "jyotiveda/grid-intel", {"transformer_id": dt_id, **risk}, dt_id
            )
        )
        # ---- budget over the next scarcity window ---------------------------------------------
        ahead = np.nonzero(gap.p_shortage[t:] >= 0.3)[0]
        w = slice(t + int(ahead[0]), t + int(ahead[-1]) + 1) if ahead.size else slice(t, min(t + 4, s.T))
        debt = {hid: float(rt.ledger.debt[i]) for i, hid in enumerate(rt.ledger.household_ids)}
        offers = s._flex_offers(w, debt)
        budget = build_budget(
            transformer=dt_id,
            window_start=s.index[w.start].to_pydatetime(),
            window_end=s.index[w.stop - 1].to_pydatetime(),
            shortage_probability=float(gap.p_shortage[w].max(initial=0)),
            expected_gap_kwh=float(gap.expected_shortage_kw[w].sum() * dt),
            p90_gap_kwh=float(gap.p90_shortage_kw[w].sum() * dt),
            critical_kwh=float((dem.p50 * (shares["t0"] + shares["t1"]))[w].sum() * dt),
            battery=env,
            battery_window_kwh_limit=(b.max_discharge_kw * (w.stop - w.start) * dt) if b else 0.0,
            p2p_kwh=float(np.maximum(sol.p50 - dem.p50, 0)[t : w.start].sum() * dt),
            flex_offered_kwh=float(sum(o.energy_kwh for o in offers)),
            flex_price_inr=float(np.median([o.price_per_kwh for o in offers])) if offers else 6.0,
            auto_shift_kwh=float((dem.p50 * shares["t3"])[w].sum() * dt * 0.3),
            discretionary_kwh=float((dem.p50 * shares["t4"])[w].sum() * dt),
        )
        RELIABILITY_GAP_KWH.labels(dt_id).set(budget.required_kwh)
        await self.bus.publish(
            CloudEvent.of(Topic.BUDGET_CREATED, "jyotiveda/reliability", budget.to_dict(), dt_id)
        )
        need = sum(a.energy_kwh for a in budget.allocation if a.resource == "flexibility_market")
        market = clear_market(offers, need, debt)
        if market.accepted:
            await self.bus.publish(
                CloudEvent.of(Topic.FLEXIBILITY_ACCEPTED, "jyotiveda/flexibility", market.to_dict(), dt_id)
            )
        # ---- optimise --------------------------------------------------------------------------
        rem = slice(t, s.T)
        z, probs = scenario_set(self.settings.mpc_scenarios)
        dscen = np.stack([dem.sample(zz)[rem] for zz in z])
        flex = np.zeros(s.T)
        flex[w] += market.cleared_kwh / max((w.stop - w.start) * dt, dt)
        flex += dem.p50 * shares["t3"] * 0.3
        plan = solve_mpc(
            MPCInputs(
                dt_h=dt,
                protected_kw=dscen * (shares["t0"] + shares["t1"])[rem],
                livelihood_kw=dscen * shares["t2"][rem],
                other_kw=dscen * (shares["t3"] + shares["t4"])[rem],
                flexible_kw=np.minimum(flex[rem], (dem.p50 * shares["t3"])[rem]),
                solar_kw=np.stack([sol.sample(-zz)[rem] for zz in z]),
                grid_cap_kw=np.tile(cap[rem], (len(z), 1)),
                tariff_inr=s.tariff[rem],
                probs=probs,
                dt_rating_kw=s.nb.transformer.rating_kw,
                battery_capacity_kwh=b.capacity_kwh if b else 0.0,
                soc0=soc,
                soc_min=b.soc_min if b else 0.0,
                soc_max=b.soc_max if b else 1.0,
                p_charge_kw=b.max_charge_kw if b else 0.0,
                p_discharge_kw=b.max_discharge_kw if b else 0.0,
                degradation_inr_per_kwh=env.degradation_cost_inr_per_kwh if env else 0.0,
                shift_in_max_kw=np.where(gap.p_shortage[rem] >= 0.3, 0.0, 60.0) if s.T - t > 1 else None,
            ),
            self.settings.mpc_solver,
        )
        first = plan.first_action()
        obs = np.zeros(13, dtype=np.float32)
        batt_kw, pol = self.policy.propose(obs, first["battery_kw"], b.max_discharge_kw if b else 0.0)
        # ---- lifeline-mode load limits if MPC expects comfort-load curtailment now --------------
        limits: dict[str, float] = {}
        if plan.expected_unserved_other_kw[0] > 0.5:
            weights = rt.ledger.weights()
            disc = s.demand.t4[t] * weights
            for j in np.argsort(-disc)[: int(0.5 * s.H)]:
                hh = s.nb.households[j]
                limits[hh.id] = hh.lifeline_kw + hh.critical_kw + hh.livelihood_kw
        live = rt.live_state(now)
        rt.gateway.telemetry(live["net_import_kw"], 0.97, 1.01, live["grid_cap_kw"])
        state = await rt.gateway.local_state(datetime.now(UTC))
        self.orchestrator.shield = self.shields[dt_id]
        explanation = {
            "why": f"Reliability gap P={budget.shortage_probability:.0%} ({budget.required_kwh:.0f} kWh P90) in "
            f"{budget.window_start:%H:%M}–{budget.window_end:%H:%M}; risk {risk['level']}",
            "shadow_price_inr_per_kwh": round(float(plan.shadow_price_energy_inr[0]), 2),
            "policy": pol,
            "budget_allocation": [a.__dict__ for a in budget.allocation],
        }
        action = ProposedAction(
            action_id=f"{dt_id}-{s.index[t]:%Y%m%dT%H%M}-{int(time.time())}",
            issued_at=datetime.now(UTC),
            valid_for_s=900,
            battery_kw=float(batt_kw),
            load_limits_kw=limits,
            flex_shift_kw=first["shift_out_kw"],
            duration_s=900,
            source=pol["policy"] if pol["policy"] != "mpc-only" else "mpc",
        )
        rec = await self.orchestrator.propose(
            dt_id, action, state, reason=explanation["why"], principal=principal, explanation=explanation
        )
        rt.gateway.load_plan(
            plan_from_mpc(
                datetime.now(UTC), self.settings.slot_minutes * 60, [float(x) for x in plan.battery_kw]
            )
        )
        protected_ok = float(1 - plan.expected_unserved_protected_kw.sum() / max(dscen.mean(0).sum(), 1e-6))
        CRITICAL_PROTECTED.labels(dt_id).set(protected_ok)
        CONTROL_CYCLE_SECONDS.labels(dt_id).observe(time.perf_counter() - t_start)
        return {
            "transformer_id": dt_id,
            "slot": s.index[t].isoformat(),
            "cycle_s": round(time.perf_counter() - t_start, 3),
            "forecast": fc["meta"],
            "risk": risk,
            "reliability_budget": budget.to_dict(),
            "flexibility_market": market.to_dict(),
            "plan": plan.to_dict(s.index[rem]) | {"schedule": plan.to_dict(s.index[rem])["schedule"][:16]},
            "policy": pol,
            "dispatch": rec.to_dict(),
            "edge_mode": rt.gateway.mode.value,
        }

    # ------------------------------------------------------------------ simulation ------
    async def simulate(self, spec: ScenarioSpec) -> dict:
        res = await asyncio.to_thread(lambda: TwinSimulator(spec, self.settings).run())
        full = res.to_dict(include_series=True)
        summary = {
            "improvement": res.improvement,
            "jyotiveda": {k: res.jyotiveda[k] for k in list(res.jyotiveda)[:6]},
        }
        self.simulations[res.id] = full
        await self.db.save_simulation(
            res.id, spec.transformer_id, spec.model_dump(mode="json"), summary, full
        )
        await self.bus.publish(
            CloudEvent.of(
                Topic.SIMULATION_COMPLETED, "jyotiveda/twin", {"id": res.id, **summary}, spec.transformer_id
            )
        )
        return full
