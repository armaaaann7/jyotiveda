"""REST API v1. Each router maps to one deployable service role (see config.ServiceRole)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jyotiveda.audit.ledger import AuditLedger
from jyotiveda.config import get_settings
from jyotiveda.events import CloudEvent, Topic
from jyotiveda.events.envelope import CloudEvent as CE
from jyotiveda.flexibility.market import FlexOffer, clear_market
from jyotiveda.runtime.platform import Platform
from jyotiveda.security.rbac import Perm, Principal, Role, mint_dev_token, require
from jyotiveda.twin.scenario import ScenarioSpec


def platform(request: Request) -> Platform:
    return request.app.state.platform


P = Annotated[Platform, Depends(platform)]


def _rt(p: Platform, dt_id: str, principal: Principal | None = None):
    if dt_id not in p.fleet:
        raise HTTPException(404, f"unknown transformer {dt_id}")
    if principal is not None and not principal.in_scope(dt_id):
        raise HTTPException(403, "transformer outside your scope")
    return p.fleet[dt_id]


# ----------------------------------------------------------------------------- auth (dev) ----
auth = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class DevTokenRequest(BaseModel):
    sub: str = "demo.operator@discom.in"
    role: Role = Role.DISCOM_OPERATOR
    transformers: list[str] = []
    household_id: str | None = None


@auth.post("/dev-token", summary="Mint a dev JWT (disabled when auth_mode=oidc)")
async def dev_token(body: DevTokenRequest) -> dict:
    s = get_settings()
    if s.auth_mode != "dev" or s.is_prod:
        raise HTTPException(404)
    return {
        "access_token": mint_dev_token(
            body.sub, body.role, s, transformers=body.transformers, household_id=body.household_id
        ),
        "token_type": "bearer",
        "role": body.role,
    }


# ----------------------------------------------------------------------------- transformers --
transformers = APIRouter(prefix="/api/v1/transformers", tags=["twin"])


@transformers.get("", summary="Fleet overview ranked by stress (DISCOM command centre)")
async def list_transformers(p: P, _: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    rows = p.fleet_overview()
    return {
        "count": len(rows),
        "transformers": rows,
        "totals": {
            "households": sum(r["households"] for r in rows),
            "renewable_share": round(
                sum(r["solar_kw"] for r in rows) / max(sum(r["demand_kw"] for r in rows), 1e-6), 3
            ),
        },
    }


@transformers.get("/{dt_id}")
async def get_transformer(dt_id: str, p: P, pr: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    rt = _rt(p, dt_id, pr)
    nb = rt.sim.nb
    return {
        "live": rt.live_state(),
        "transformer": nb.transformer.model_dump(),
        "battery": nb.battery.model_dump() if nb.battery else None,
        "households": len(nb.households),
        "rooftop_solar_kwp": nb.solar_kwp,
        "lifeline_kw": round(nb.lifeline_kw, 2),
        "edge": {
            "mode": rt.gateway.mode.value,
            "outbox": len(rt.gateway.outbox),
            "applied": rt.gateway.applied[-5:],
        },
    }


@transformers.get(
    "/{dt_id}/topology", summary="Graph for 2D map / 3D twin: nodes (DT, LV buses, homes) + edges"
)
async def topology(dt_id: str, p: P, pr: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    from jyotiveda.twin.network import FeederModel

    rt = _rt(p, dt_id, pr)
    topo = FeederModel(rt.sim.nb).topology()
    geo = {h.id: (h.lat, h.lon) for h in rt.sim.nb.households}
    for n in topo["nodes"]:
        if n["id"] in geo:
            n["lat"], n["lon"] = geo[n["id"]]
    return topo


@transformers.get("/{dt_id}/households")
async def households(
    dt_id: str,
    p: P,
    pr: Principal = Depends(require(Perm.READ_HOUSEHOLD)),
    offset: int = 0,
    limit: int = Query(50, le=500),
) -> dict:
    rt = _rt(p, dt_id, pr)
    t = rt.slot()
    s = rt.sim
    items = []
    for j, h in enumerate(s.nb.households[offset : offset + limit], start=offset):
        items.append(
            h.model_dump()
            | {
                "demand_kw": round(float(s.demand.total[t, j]), 3),
                "solar_kw": round(float(s.solar[t, j]), 3),
                "fairness_debt": round(float(rt.ledger.debt[j]), 4),
                "fairness_level": rt.ledger.level(j),
            }
        )
    return {"total": len(s.nb.households), "offset": offset, "items": items}


class ClockBody(BaseModel):
    hour: float = Field(ge=0, lt=24, description="IST hour of day to jump the twin to (demo time-travel)")


@transformers.post("/{dt_id}/clock", summary="Demo time-travel: move this twin's clock (dev/operator)")
async def set_clock(
    dt_id: str, body: ClockBody, p: P, pr: Principal = Depends(require(Perm.RUN_SIMULATION))
) -> dict:
    rt = _rt(p, dt_id, pr)
    rt.set_clock(body.hour)
    return rt.live_state()


# ----------------------------------------------------------------------------- forecast ------
forecast = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


@forecast.get("/{dt_id}", summary="Probabilistic demand / solar / reliability-gap forecast (P10/P50/P90)")
async def get_forecast(
    dt_id: str,
    p: P,
    pr: Principal = Depends(require(Perm.READ_GRID)),
    kind: str = Query("all", pattern="^(demand|solar|gap|all)$"),
) -> dict:
    rt = _rt(p, dt_id, pr)
    fc = rt.forecasts()
    out: dict = {"transformer_id": dt_id, "meta": fc["meta"]}
    if kind in ("demand", "all"):
        out["demand"] = fc["demand"].to_records()
    if kind in ("solar", "all"):
        out["solar"] = fc["solar"].to_records()
    if kind in ("gap", "all"):
        out["gap"] = fc["gap"].to_dict()
    return out


# ----------------------------------------------------------------------------- reliability ---
reliability = APIRouter(prefix="/api/v1/reliability", tags=["reliability"])


@reliability.get("/{dt_id}/risk", summary="Grid-intelligence risk (GNN / LinDistFlow)")
async def risk(dt_id: str, p: P, pr: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    return p.risk(_rt(p, dt_id, pr))


@reliability.post(
    "/{dt_id}/cycle", summary="Run the full Sense→Predict→Budget→Market→MPC→Shield→Dispatch cycle"
)
async def cycle(dt_id: str, p: P, pr: Principal = Depends(require(Perm.PROPOSE_DISPATCH))) -> dict:
    _rt(p, dt_id, pr)
    return await p.run_cycle(dt_id, pr)


# ----------------------------------------------------------------------------- flexibility ---
flexibility = APIRouter(prefix="/api/v1/flexibility", tags=["flexibility"])
_OFFERS: dict[str, FlexOffer] = {}


@flexibility.post("/offers", status_code=201, summary="Resident posts a flexibility offer")
async def post_offer(offer: FlexOffer, p: P, pr: Principal = Depends(require(Perm.OFFER_FLEX))) -> dict:
    _rt(p, offer.transformer_id)
    if pr.role == Role.RESIDENT and pr.household_id and pr.household_id != offer.household_id:
        raise HTTPException(403, "residents can only offer for their own household")
    _OFFERS[offer.id] = offer
    await p.bus.publish(
        CloudEvent.of(Topic.FLEXIBILITY_OFFERED, "jyotiveda/flexibility", offer, offer.transformer_id)
    )
    return {"id": offer.id, "status": "OPEN", "price_per_kwh": round(offer.price_per_kwh, 2)}


@flexibility.get("/offers")
async def list_offers(
    transformer_id: str | None = None, _: Principal = Depends(require(Perm.READ_GRID))
) -> list[dict]:
    return [o.model_dump(mode="json") for o in _OFFERS.values() if transformer_id in (None, o.transformer_id)]


class ClearBody(BaseModel):
    need_kwh: float = Field(gt=0)
    fairness_lambda: float = 4.0
    price_cap_inr_per_kwh: float = 15.0


@flexibility.post("/{dt_id}/clear", summary="Clear open offers against a need (fairness-aware MILP)")
async def clear(
    dt_id: str, body: ClearBody, p: P, pr: Principal = Depends(require(Perm.CLEAR_MARKET))
) -> dict:
    rt = _rt(p, dt_id, pr)
    offers = [o for o in _OFFERS.values() if o.transformer_id == dt_id]
    debt = {hid: float(rt.ledger.debt[i]) for i, hid in enumerate(rt.ledger.household_ids)}
    res = clear_market(offers, body.need_kwh, debt, body.fairness_lambda, body.price_cap_inr_per_kwh)
    await p.bus.publish(
        CloudEvent.of(Topic.FLEXIBILITY_ACCEPTED, "jyotiveda/flexibility", res.to_dict(), dt_id)
    )
    return res.to_dict()


# ----------------------------------------------------------------------------- fairness ------
fairness = APIRouter(prefix="/api/v1/fairness", tags=["fairness"])


@fairness.get("/{dt_id}", summary="Fairness Debt ledger (heatmap source) + equity indices")
async def fairness_ledger(dt_id: str, p: P, pr: Principal = Depends(require(Perm.READ_HOUSEHOLD))) -> dict:
    from jyotiveda.fairness.debt import gini, jain_index

    rt = _rt(p, dt_id, pr)
    snap = rt.ledger.snapshot()
    return {
        "transformer_id": dt_id,
        "gini_debt": round(gini(rt.ledger.debt), 4),
        "jain_index": round(jain_index(1 / (1 + rt.ledger.debt)), 4),
        "households": snap,
    }


@fairness.get("/{dt_id}/households/{hh_id}")
async def fairness_household(
    dt_id: str, hh_id: str, p: P, pr: Principal = Depends(require(Perm.READ_HOUSEHOLD))
) -> dict:
    rt = _rt(p, dt_id, pr)
    try:
        i = rt.ledger.household_ids.index(hh_id)
    except ValueError as exc:
        raise HTTPException(404, "unknown household") from exc
    row = rt.ledger.snapshot()[i]
    row["next_flexibility_request"] = "DEFERRED" if row["level"] == "HIGH" else "ELIGIBLE"
    row["reason"] = (
        "Alternative lower-burden flexibility available."
        if row["level"] == "HIGH"
        else "Normal rotation — burden below neighbourhood median."
    )
    return row


# ----------------------------------------------------------------------------- simulation ----
simulation = APIRouter(prefix="/api/v1/simulation", tags=["digital-twin"])


@simulation.post("/run", summary="Counterfactual what-if: baseline vs Jyotiveda on the same physical day")
async def run_sim(
    spec: ScenarioSpec, p: P, _: Principal = Depends(require(Perm.RUN_SIMULATION)), series: bool = True
) -> dict:
    res = await p.simulate(spec)
    if not series:
        res = {k: v for k, v in res.items() if k not in ("timeline", "plan")}
    return res


@simulation.post("/scenario/renewable-shock", summary="The 'Cloud Attack' demo: one slider, full comparison")
async def renewable_shock(
    p: P,
    _: Principal = Depends(require(Perm.RUN_SIMULATION)),
    solar_reduction: float = Query(0.6, ge=0, le=1),
    transformer_id: str = "DT-104",
) -> dict:
    rt = _rt(p, transformer_id)
    spec = rt.spec.model_copy(update={"solar_reduction": solar_reduction, "run_power_flow": True})
    return await p.simulate(ScenarioSpec.model_validate(spec.model_dump()))


@simulation.get("")
async def list_sims(p: P, _: Principal = Depends(require(Perm.READ_GRID))) -> list[dict]:
    return await p.db.list_simulations()


@simulation.get("/{sim_id}")
async def get_sim(
    sim_id: str, p: P, _: Principal = Depends(require(Perm.READ_GRID)), series: bool = False
) -> dict:
    res = p.simulations.get(sim_id) or await p.db.get_simulation(sim_id)
    if res is None:
        raise HTTPException(404)
    return res if series else {k: v for k, v in res.items() if k not in ("timeline", "plan")}


@simulation.get("/{sim_id}/results")
async def sim_results(sim_id: str, p: P, _: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    return await get_sim(sim_id, p, _, series=True)


# ----------------------------------------------------------------------------- dispatch ------
dispatch = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])


@dispatch.get("")
async def list_dispatch(
    p: P, transformer_id: str | None = None, _: Principal = Depends(require(Perm.READ_GRID))
) -> list[dict]:
    return [r.to_dict() for r in p.orchestrator.list(transformer_id)]


@dispatch.get("/{dispatch_id}")
async def get_dispatch(dispatch_id: str, p: P, _: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    rec = p.orchestrator.records.get(dispatch_id)
    if rec is None:
        raise HTTPException(404)
    return rec.to_dict()


class ApprovalBody(BaseModel):
    note: str = ""


@dispatch.post("/{dispatch_id}/approve", summary="Human-in-the-loop approval (DISCOM operator)")
async def approve(
    dispatch_id: str, body: ApprovalBody, p: P, pr: Principal = Depends(require(Perm.APPROVE_DISPATCH))
) -> dict:
    if dispatch_id not in p.orchestrator.records:
        raise HTTPException(404)
    try:
        return (await p.orchestrator.approve(dispatch_id, pr, body.note)).to_dict()
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc


@dispatch.post("/{dispatch_id}/reject")
async def reject(
    dispatch_id: str, body: ApprovalBody, p: P, pr: Principal = Depends(require(Perm.APPROVE_DISPATCH))
) -> dict:
    if dispatch_id not in p.orchestrator.records:
        raise HTTPException(404)
    try:
        return (await p.orchestrator.reject(dispatch_id, pr, body.note or "operator rejected")).to_dict()
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc


@dispatch.post("/emergency/{dt_id}", summary="Urja Sakhi manual emergency mode (edge lifeline controller)")
async def emergency(
    dt_id: str, p: P, pr: Principal = Depends(require(Perm.EMERGENCY_MODE)), enable: bool = True
) -> dict:
    from jyotiveda.edge.gateway import EdgeMode

    rt = _rt(p, dt_id, pr)
    rt.gateway.mode = EdgeMode.MANUAL_EMERGENCY if enable else EdgeMode.CLOUD_COORDINATED
    await p.ledger.append(
        pr.sub, pr.role.value, "edge.emergency_mode", "transformer", dt_id, "manual", {"enabled": enable}
    )
    res = await rt.gateway.tick()
    return {"transformer_id": dt_id, **res}


# ----------------------------------------------------------------------------- edge ----------
edge = APIRouter(prefix="/api/v1/edge", tags=["edge"])


@edge.get("/{dt_id}")
async def edge_status(dt_id: str, p: P, pr: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    rt = _rt(p, dt_id, pr)
    g = rt.gateway
    return {
        "mode": g.mode.value,
        "cloud_online": g.cloud_online(datetime.now(UTC)),
        "outbox_depth": len(g.outbox),
        "cached_plan_until": g.plan.valid_until.isoformat() if g.plan else None,
        "recent_actions": g.applied[-10:],
        "battery": await g.pcs.read(),
    }


@edge.post("/{dt_id}/simulate-outage", summary="Chaos test: cut the cloud link and watch edge autonomy")
async def simulate_outage(
    dt_id: str,
    p: P,
    pr: Principal = Depends(require(Perm.RUN_SIMULATION)),
    seconds: int = Query(300, ge=60, le=86400),
) -> dict:
    rt = _rt(p, dt_id, pr)
    rt.gateway.last_cloud_contact = datetime.now(UTC) - timedelta(seconds=seconds)
    res = await rt.gateway.tick()
    await p.bus.publish(
        CloudEvent.of(
            Topic.ALARM_RAISED,
            "jyotiveda/edge",
            {"transformer_id": dt_id, "alarm": "CLOUD_LINK_LOST", "edge_response": res},
            dt_id,
        )
    )
    return {"transformer_id": dt_id, "cloud_link": "DOWN", "edge_response": res}


# ----------------------------------------------------------------------------- audit ---------
audit = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@audit.get("")
async def audit_log(
    p: P,
    entity_id: str | None = None,
    limit: int = Query(100, le=1000),
    _: Principal = Depends(require(Perm.READ_AUDIT)),
) -> list[dict]:
    return [r.to_dict() for r in p.ledger.records(entity_id, limit)]


@audit.get("/verify", summary="Verify the hash chain end-to-end (tamper evidence)")
async def audit_verify(p: P, _: Principal = Depends(require(Perm.READ_AUDIT))) -> dict:
    ok, bad = AuditLedger.verify(p.ledger.records(limit=10**9))
    return {"valid": ok, "first_invalid_seq": bad, "records": len(p.ledger), "head": p.ledger.head}


# ----------------------------------------------------------------------------- copilot -------
copilot = APIRouter(prefix="/api/v1/copilot", tags=["copilot"])


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    transformer_id: str | None = None
    history: list[dict] = []


@copilot.post("/chat", summary="Jyoti Copilot (Claude tool-use agent, multilingual)")
async def chat(body: ChatBody, request: Request, pr: Principal = Depends(require(Perm.USE_COPILOT))) -> dict:
    return await request.app.state.copilot.chat(body.message, pr, body.transformer_id, body.history)


# ----------------------------------------------------------------------------- analytics -----
analytics = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@analytics.get("/summary", summary="Headline KPIs for the command centre header")
async def summary(p: P, _: Principal = Depends(require(Perm.READ_GRID))) -> dict:
    rows = p.fleet_overview()
    sims = await p.db.list_simulations(50)
    imp = [
        s["summary"]["improvement"]["critical_outage_reduction_pct"]
        for s in sims
        if s["summary"]["improvement"].get("critical_outage_reduction_pct") is not None
    ]
    return {
        "transformers": len(rows),
        "households": sum(r["households"] for r in rows),
        "renewable_share": round(
            sum(r["solar_kw"] for r in rows) / max(sum(r["demand_kw"] for r in rows), 1e-6), 3
        ),
        "at_risk": sum(1 for r in rows if r["risk"]["level"] in ("HIGH", "CRITICAL")),
        "fleet_battery_soc": round(
            sum(r["battery"]["soc"] * r["battery"]["capacity_kwh"] for r in rows)
            / max(sum(r["battery"]["capacity_kwh"] for r in rows), 1e-6),
            3,
        ),
        "dispatches": len(p.orchestrator.records),
        "avg_simulated_critical_outage_reduction_pct": round(sum(imp) / len(imp), 1) if imp else None,
        "uptime_s": int((datetime.now(UTC) - p.started_at).total_seconds()),
    }


# ----------------------------------------------------------------------------- events --------
events = APIRouter(prefix="/api/v1/events", tags=["events"])


@events.get("/recent")
async def recent(
    p: P,
    limit: int = Query(100, le=1000),
    topic: str | None = None,
    _: Principal = Depends(require(Perm.READ_GRID)),
) -> list[dict]:
    return [e.model_dump(mode="json") for e in p.bus.recent(limit * 3) if topic in (None, str(e.type))][
        -limit:
    ]


@events.get("/schemas", summary="Event catalogue: CloudEvents envelope JSON Schema + topics")
async def schemas() -> dict:
    return {"envelope": CE.model_json_schema(), "topics": [t.value for t in Topic]}
