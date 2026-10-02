"""Jyotiveda Edge Energy Gateway — the local brain at every transformer.

Cloud does planning; edge does protection. The gateway:
  * verifies every command (Ed25519 signature, validity window, nonce replay)
  * re-validates it with the SAME SafetyShield code against *local, fresh* telemetry
  * applies setpoints to the PCS and smart-meter load limiters, reports ACK / EXECUTED
  * buffers telemetry in a durable SQLite outbox (store-and-forward) while the uplink is down
  * runs autonomously when the cloud is unreachable:
        cloud lost → follow cached day-ahead plan → plan expired → rule-based lifeline controller
    and resynchronises when the link returns.
  * never dispatches the battery on missing/stale BMS telemetry (fail-safe idle)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import orjson
import structlog

from jyotiveda.safety.shield import GridState, ProposedAction, SafetyShield, Verdict
from jyotiveda.security.signing import CommandVerifier

log = structlog.get_logger(__name__)


class EdgeMode(StrEnum):
    CLOUD_COORDINATED = "CLOUD_COORDINATED"
    AUTONOMOUS_CACHED_PLAN = "AUTONOMOUS_CACHED_PLAN"
    AUTONOMOUS_LIFELINE = "AUTONOMOUS_LIFELINE"
    MANUAL_EMERGENCY = "MANUAL_EMERGENCY"


@dataclass
class CachedPlan:
    start: datetime
    slot_s: int
    battery_kw: list[float]
    valid_until: datetime

    def setpoint(self, now: datetime) -> float | None:
        if now > self.valid_until or now < self.start:
            return None
        k = int((now - self.start).total_seconds() // self.slot_s)
        return self.battery_kw[k] if 0 <= k < len(self.battery_kw) else None


class Outbox:
    """Durable store-and-forward queue (SQLite WAL) — telemetry is never lost during outages."""

    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, body BLOB)"
        )

    def put(self, topic: str, body: dict) -> None:
        self.db.execute("INSERT INTO outbox(topic, body) VALUES (?, ?)", (topic, orjson.dumps(body)))
        self.db.commit()

    def drain(self, limit: int = 500) -> list[tuple[int, str, dict]]:
        rows = self.db.execute("SELECT id, topic, body FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [(r[0], r[1], orjson.loads(r[2])) for r in rows]

    def ack(self, ids: list[int]) -> None:
        self.db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    def __len__(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


@dataclass
class EdgeGateway:
    transformer_id: str
    verifier: CommandVerifier
    shield: SafetyShield
    pcs: object  # PCSDriver
    meters: object  # MeterDriver
    lifeline_kw_by_household: dict[str, float]
    outbox: Outbox = field(default_factory=Outbox)
    cloud_timeout_s: float = 90.0
    last_cloud_contact: datetime = field(default_factory=lambda: datetime.now(UTC))
    plan: CachedPlan | None = None
    mode: EdgeMode = EdgeMode.CLOUD_COORDINATED
    dt_load_kw: float = 0.0
    supply_cap_kw: float | None = None
    v_min_pu: float = 1.0
    v_max_pu: float = 1.0
    telemetry_ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    applied: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ state ----------
    async def local_state(self, now: datetime) -> GridState:
        b = await self.pcs.read()
        return GridState(
            ts=now,
            battery_soc=b["soc"],
            battery_soh=1.0,
            battery_capacity_kwh=getattr(self.pcs, "capacity_kwh", 200.0),
            battery_temp_c=b["temperature_c"],
            battery_power_kw=b["power_kw"],
            battery_alarms=tuple(getattr(self.pcs, "alarms", [])),
            dt_load_kw=self.dt_load_kw,
            v_min_pu=self.v_min_pu,
            v_max_pu=self.v_max_pu,
            telemetry_age_s=(now - self.telemetry_ts).total_seconds(),
            lifeline_kw_by_household=self.lifeline_kw_by_household,
        )

    def heartbeat(self, now: datetime | None = None) -> None:
        self.last_cloud_contact = now or datetime.now(UTC)
        if self.mode != EdgeMode.MANUAL_EMERGENCY:
            self.mode = EdgeMode.CLOUD_COORDINATED

    def cloud_online(self, now: datetime) -> bool:
        return (now - self.last_cloud_contact).total_seconds() <= self.cloud_timeout_s

    # ------------------------------------------------------------------ commands -------
    async def handle_command(self, signed: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        ok, why = self.verifier.verify(signed, now)
        if not ok:
            log.warning("edge_command_rejected", reason=why, command=signed.get("command_id"))
            return {"accepted": False, "detail": why}
        if signed["transformer_id"] != self.transformer_id:
            return {"accepted": False, "detail": "wrong transformer"}
        self.heartbeat(now)
        action = ProposedAction(
            action_id=signed["command_id"],
            issued_at=datetime.fromisoformat(signed["not_before"]),
            valid_for_s=(
                datetime.fromisoformat(signed["expires_at"]) - datetime.fromisoformat(signed["not_before"])
            ).total_seconds(),
            battery_kw=float(signed["battery_kw"]),
            load_limits_kw=dict(signed.get("load_limits_kw", {})),
            flex_shift_kw=float(signed.get("flex_shift_kw", 0.0)),
            duration_s=float(signed.get("duration_s", 900)),
            source="cloud",
        )
        dec = self.shield.validate(action, await self.local_state(now), now=now)
        if dec.verdict == Verdict.REJECTED or dec.action is None:
            return {"accepted": False, "detail": "local shield rejected", "violations": dec.violations}
        applied = await self._apply(dec.action)
        return {
            "accepted": applied,
            "detail": dec.verdict.value,
            "violations": dec.violations,
            "applied_battery_kw": dec.action.battery_kw,
        }

    async def _apply(self, a: ProposedAction) -> bool:
        ok = await self.pcs.set_power(a.battery_kw)
        for hh, kw in a.load_limits_kw.items():
            ok &= await self.meters.set_load_limit(hh, kw * 1000)
        self.applied.append(
            {
                "action_id": a.action_id,
                "battery_kw": a.battery_kw,
                "limits": len(a.load_limits_kw),
                "at": datetime.now(UTC).isoformat(),
                "mode": self.mode.value,
            }
        )
        return bool(ok)

    def load_plan(self, plan: CachedPlan) -> None:
        self.plan = plan

    # ------------------------------------------------------------------ autonomy -------
    async def tick(self, now: datetime | None = None) -> dict:
        """Called every few seconds by the edge runtime. Enforces autonomy when the cloud is gone."""
        now = now or datetime.now(UTC)
        if self.mode == EdgeMode.MANUAL_EMERGENCY:
            return await self._lifeline_controller(now, reason="manual emergency mode (Urja Sakhi)")
        if self.cloud_online(now):
            self.mode = EdgeMode.CLOUD_COORDINATED
            return {"mode": self.mode.value}
        sp = self.plan.setpoint(now) if self.plan else None
        if sp is not None:
            self.mode = EdgeMode.AUTONOMOUS_CACHED_PLAN
            # correct cached setpoint with live deficit (edge sees real load, cloud plan may be stale)
            if self.supply_cap_kw is not None and self.dt_load_kw - sp > self.supply_cap_kw:
                sp = self.dt_load_kw - self.supply_cap_kw
            return await self._local_action(now, sp, "edge-cached-plan")
        return await self._lifeline_controller(now, reason="cloud unreachable and plan expired")

    async def _lifeline_controller(self, now: datetime, reason: str) -> dict:
        if self.mode != EdgeMode.MANUAL_EMERGENCY:
            self.mode = EdgeMode.AUTONOMOUS_LIFELINE
        cap = self.supply_cap_kw if self.supply_cap_kw is not None else float("inf")
        need = max(self.dt_load_kw - cap, 0.0)
        limits = {hh: floor for hh, floor in self.lifeline_kw_by_household.items()} if need > 0 else {}
        res = await self._local_action(now, need, "edge-fallback", limits)
        res["reason"] = reason
        return res

    async def _local_action(
        self, now: datetime, battery_kw: float, source: str, limits: dict | None = None
    ) -> dict:
        a = ProposedAction(
            action_id=f"edge-{now.timestamp():.0f}",
            issued_at=now,
            valid_for_s=300,
            battery_kw=battery_kw,
            load_limits_kw=limits or {},
            duration_s=300,
            source=source,
        )
        dec = self.shield.validate(a, await self.local_state(now), now=now)
        if dec.action is not None:
            await self._apply(dec.action)
        self.outbox.put(
            "edge.action", {"ts": now.isoformat(), "mode": self.mode.value, "shield": dec.to_dict()}
        )
        return {
            "mode": self.mode.value,
            "battery_kw": dec.action.battery_kw if dec.action else 0.0,
            "verdict": dec.verdict.value,
            "violations": dec.violations,
        }

    def telemetry(
        self,
        dt_load_kw: float,
        v_min: float,
        v_max: float,
        supply_cap_kw: float | None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(UTC)
        self.dt_load_kw, self.v_min_pu, self.v_max_pu, self.supply_cap_kw = (
            dt_load_kw,
            v_min,
            v_max,
            supply_cap_kw,
        )
        self.telemetry_ts = now
        self.outbox.put(
            "transformer.state",
            {
                "ts": now.isoformat(),
                "transformer_id": self.transformer_id,
                "load_kw": dt_load_kw,
                "v_min_pu": v_min,
                "v_max_pu": v_max,
            },
        )


class InProcessEdgeTransport:
    """Loopback transport (dev/demo/CI): orchestrator → gateway in the same process, with ACK/EXECUTED callbacks."""

    def __init__(self, gateways: dict[str, EdgeGateway]) -> None:
        self.gateways = gateways
        self.orchestrator = None  # set after construction

    async def send(self, transformer_id: str, signed_command: dict) -> bool:
        gw = self.gateways.get(transformer_id)
        if gw is None:
            return False
        res = await gw.handle_command(signed_command)
        if self.orchestrator is not None:
            await self.orchestrator.on_edge_ack(
                signed_command["command_id"], res["accepted"], res.get("detail", "")
            )
            if res["accepted"]:
                measured = (await gw.pcs.read())["power_kw"]
                await self.orchestrator.on_executed(signed_command["command_id"], measured)
        return bool(res["accepted"])


class MqttEdgeTransport:  # pragma: no cover - requires a broker (EMQX in docker-compose)
    """QoS-1 MQTT 5 transport: jyotiveda/v1/dt/{id}/cmd  →  gateway;  .../ack  → orchestrator."""

    def __init__(self, host: str, port: int = 1883) -> None:
        self.host, self.port = host, port

    async def send(self, transformer_id: str, signed_command: dict) -> bool:
        import aiomqtt

        async with aiomqtt.Client(self.host, self.port, identifier="jyotiveda-dispatch") as c:
            await c.publish(f"jyotiveda/v1/dt/{transformer_id}/cmd", orjson.dumps(signed_command), qos=1)
        return True


def plan_from_mpc(
    start: datetime, slot_s: int, battery_kw: list[float], hours_valid: float = 24
) -> CachedPlan:
    return CachedPlan(
        start=start, slot_s=slot_s, battery_kw=battery_kw, valid_until=start + timedelta(hours=hours_valid)
    )
