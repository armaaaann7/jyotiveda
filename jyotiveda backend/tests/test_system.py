"""System tests: counterfactual twin, dispatch lifecycle, edge autonomy, grid intelligence, API + WebSocket."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from jyotiveda.config import Settings
from jyotiveda.twin.scenario import ScenarioSpec, SupplyWindow
from jyotiveda.twin.simulator import TwinSimulator
from tests.conftest import token

FAST = Settings(env="test", forecast_backend="seasonal", log_json=False)


# ------------------------------------------------------------------ digital twin -------------
def test_jyotiveda_beats_baseline_on_reference_day():
    res = TwinSimulator(ScenarioSpec(run_power_flow=False), FAST).run()
    b, j, imp = res.baseline, res.jyotiveda, res.improvement
    assert b["critical_outage_home_hours"] > 50
    assert imp["critical_outage_reduction_pct"] >= 70  # PRD target
    assert imp["household_outage_reduction_pct"] >= 40
    assert j["lifeline_availability_in_scarcity"] >= 0.97
    assert j["fairness_gini_served_ratio"] <= b["fairness_gini_served_ratio"]
    assert res.budget["critical_loads_protected"]
    kinds = [e["event"] for e in res.events]
    assert "RELIABILITY_BUDGET_CREATED" in kinds and "BATTERY_ACTIVE" in kinds


def test_extreme_shock_still_improves_and_never_curtails_lifeline_first():
    spec = ScenarioSpec(
        solar_reduction=0.95,
        battery_kwh=60,
        battery_kw=30,
        run_power_flow=False,
        supply_windows=[SupplyWindow(start_hour=17, end_hour=23, cap_kw=25)],
    )
    sim = TwinSimulator(spec, FAST)
    base = sim.run_baseline()
    jv, _ = sim.run_jyotiveda()
    kb, kj = sim.kpis(base), sim.kpis(jv)
    assert kj["critical_outage_home_hours"] < kb["critical_outage_home_hours"]
    # whenever any home loses lifeline, all discretionary load in the DT was already curtailed
    for t in range(sim.T):
        if (jv.served[t] < jv.protected[t] - 1e-6).any():
            assert (
                jv.served[t][~jv.dark[t]].sum()
                <= jv.protected[t][~jv.dark[t]].sum() + jv.demand[t].sum() * 0.5
            )


def test_power_flow_runs_and_reports_voltage():
    res = TwinSimulator(ScenarioSpec(run_power_flow=True, n_connections=60), FAST).run()
    assert 0.9 < res.jyotiveda["v_min_pu"] <= 1.0
    assert res.timeline[0]["jyotiveda"]["v_min_pu"] > 0.9


def test_physics_risk_close_to_ac_power_flow():
    from jyotiveda.gridintel.graph import build_graph
    from jyotiveda.gridintel.risk import PhysicsRiskEngine
    from jyotiveda.twin.neighbourhood import build_neighbourhood
    from jyotiveda.twin.network import FeederModel

    nb = build_neighbourhood()
    d = np.random.default_rng(1).uniform(0.4, 1.6, len(nb.households))
    z = np.zeros(len(nb.households))
    est = PhysicsRiskEngine().score(build_graph(nb, d, z, z + 0.25)).v_min_estimate_pu
    ac = FeederModel(nb).solve(d, z).v_min_pu
    assert abs(est - ac) < 0.015


# ------------------------------------------------------------------ edge autonomy ------------
async def test_edge_autonomy_modes():
    from jyotiveda.edge.gateway import EdgeGateway, EdgeMode, plan_from_mpc
    from jyotiveda.edge.protocols import VirtualMeterBank, VirtualPCS
    from jyotiveda.safety.shield import SafetyLimits, SafetyShield
    from jyotiveda.security.signing import CommandSigner, CommandVerifier

    signer = CommandSigner.from_path_or_ephemeral(None)
    gw = EdgeGateway(
        "DT-1",
        CommandVerifier(signer.public_pem()),
        SafetyShield(SafetyLimits()),
        VirtualPCS(),
        VirtualMeterBank(),
        {f"H{i}": 0.25 for i in range(10)},
    )
    now = datetime.now(UTC)
    gw.telemetry(120, 0.97, 1.01, 80, now)
    assert (await gw.tick(now))["mode"] == EdgeMode.CLOUD_COORDINATED
    later = now + timedelta(seconds=600)
    gw.telemetry(120, 0.97, 1.01, 80, later)
    gw.load_plan(plan_from_mpc(now, 900, [20.0] * 96))
    r = await gw.tick(later)
    assert r["mode"] == EdgeMode.AUTONOMOUS_CACHED_PLAN and r["battery_kw"] == pytest.approx(
        40
    )  # corrected to live deficit
    gw.plan = None
    r = await gw.tick(later)
    assert r["mode"] == EdgeMode.AUTONOMOUS_LIFELINE and len(gw.outbox) >= 2
    # stale telemetry -> battery forced idle even in autonomy
    r = await gw.tick(later + timedelta(seconds=600))
    assert r["battery_kw"] == 0 and any(v["rule"] == "S2" for v in r["violations"])


# ------------------------------------------------------------------ API ----------------------
def test_auth_and_rbac(client):
    assert client.get("/api/v1/transformers").status_code == 401
    res = token(client, "RESIDENT")
    assert client.get("/api/v1/transformers", headers=res).status_code == 403
    assert client.get("/healthz").json() == {"status": "ok"}


def test_full_cycle_with_human_approval_and_verification(client):
    op = token(client)
    client.post("/api/v1/transformers/DT-104/clock", json={"hour": 19.0}, headers=op)
    r = client.post("/api/v1/reliability/DT-104/cycle", headers=op)
    assert r.status_code == 200, r.text
    d = r.json()["dispatch"]
    assert d["shield"]["verdict"] in ("APPROVED", "MODIFIED")
    if d["state"] == "AWAITING_APPROVAL":
        analyst = token(client, "ANALYST")
        assert client.post(f"/api/v1/dispatch/{d['id']}/approve", json={}, headers=analyst).status_code == 403
        d = client.post(
            f"/api/v1/dispatch/{d['id']}/approve", json={"note": "evening gap"}, headers=op
        ).json()
    assert d["state"] == "VERIFIED", d["history"]
    states = [h["to"] for h in d["history"]]
    assert states[:2] == ["PROPOSED", "VALIDATING"] and states[-4:] == [
        "SENT",
        "ACKNOWLEDGED",
        "EXECUTED",
        "VERIFIED",
    ]
    assert d["command"]["kid"]
    v = client.get("/api/v1/audit/verify", headers=op).json()
    assert v["valid"] and v["records"] >= 7


def test_simulation_api_and_copilot_and_ws(client):
    op = token(client)
    r = client.post(
        "/api/v1/simulation/run?series=false", json={"run_power_flow": False, "n_connections": 80}, headers=op
    )
    assert r.status_code == 200 and r.json()["improvement"]["critical_outage_reduction_pct"] is not None
    sim_id = r.json()["id"]
    assert client.get(f"/api/v1/simulation/{sim_id}", headers=op).status_code == 200
    assert len(client.get("/api/v1/simulation", headers=op).json()) >= 1
    c = client.post(
        "/api/v1/copilot/chat", json={"message": "which transformer will overload tomorrow?"}, headers=op
    ).json()
    assert "DT-" in c["answer"]
    fair = client.get("/api/v1/fairness/DT-104", headers=op).json()
    assert len(fair["households"]) == 180
    tok = op["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/live?token={tok}") as ws:
        assert ws.receive_json()["type"] == "hello"
        evt = ws.receive_json()
        assert evt["specversion"] == "1.0"


def test_emergency_mode_and_outage_chaos(client):
    sakhi = token(client, "URJA_SAKHI")
    r = client.post("/api/v1/dispatch/emergency/DT-104", headers=sakhi).json()
    assert r["mode"] == "MANUAL_EMERGENCY"
    op = token(client)
    r = client.post("/api/v1/edge/DT-101/simulate-outage?seconds=900", headers=op).json()
    assert r["edge_response"]["mode"].startswith("AUTONOMOUS")
