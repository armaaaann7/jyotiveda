"""dispatch-orchestrator: the only path from a decision to a device.

    AI → Optimisation → Safety Shield → Dispatch Orchestrator → Policy/RBAC → signed command
       → Edge Gateway (re-validates locally) → Device → Telemetry → Verification

Lifecycle (explicit state machine; every transition is evented, metered and hash-chain audited):

    PROPOSED → VALIDATING ─┬→ REJECTED
                           ├→ AWAITING_APPROVAL → APPROVED | REJECTED | EXPIRED
                           └→ APPROVED → SENT → ACKNOWLEDGED → EXECUTED → VERIFIED | FAILED
    (any non-terminal) → CANCELLED | EXPIRED
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

import structlog

from jyotiveda.audit.ledger import AuditLedger
from jyotiveda.events import CloudEvent, EventBus, Topic
from jyotiveda.observability import DISPATCH_TRANSITIONS, SHIELD_VERDICTS, SHIELD_VIOLATIONS
from jyotiveda.safety.shield import GridState, ProposedAction, SafetyShield, ShieldDecision, Verdict
from jyotiveda.security.rbac import Perm, Principal
from jyotiveda.security.signing import CommandSigner

log = structlog.get_logger(__name__)


class DispatchState(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


TERMINAL = {
    DispatchState.VERIFIED,
    DispatchState.FAILED,
    DispatchState.REJECTED,
    DispatchState.EXPIRED,
    DispatchState.CANCELLED,
}
TRANSITIONS: dict[DispatchState, set[DispatchState]] = {
    DispatchState.PROPOSED: {DispatchState.VALIDATING, DispatchState.CANCELLED},
    DispatchState.VALIDATING: {
        DispatchState.REJECTED,
        DispatchState.AWAITING_APPROVAL,
        DispatchState.APPROVED,
    },
    DispatchState.AWAITING_APPROVAL: {
        DispatchState.APPROVED,
        DispatchState.REJECTED,
        DispatchState.EXPIRED,
        DispatchState.CANCELLED,
    },
    DispatchState.APPROVED: {DispatchState.SENT, DispatchState.EXPIRED, DispatchState.CANCELLED},
    DispatchState.SENT: {DispatchState.ACKNOWLEDGED, DispatchState.FAILED, DispatchState.EXPIRED},
    DispatchState.ACKNOWLEDGED: {DispatchState.EXECUTED, DispatchState.FAILED},
    DispatchState.EXECUTED: {DispatchState.VERIFIED, DispatchState.FAILED},
}
TOPIC_OF = {
    DispatchState.PROPOSED: Topic.DISPATCH_PROPOSED,
    DispatchState.VALIDATING: Topic.DISPATCH_VALIDATED,
    DispatchState.APPROVED: Topic.DISPATCH_APPROVED,
    DispatchState.SENT: Topic.DISPATCH_SENT,
    DispatchState.ACKNOWLEDGED: Topic.DISPATCH_ACKNOWLEDGED,
    DispatchState.EXECUTED: Topic.DISPATCH_EXECUTED,
    DispatchState.VERIFIED: Topic.DISPATCH_VERIFIED,
    DispatchState.REJECTED: Topic.DISPATCH_REJECTED,
}


class IllegalTransition(Exception):
    pass


@dataclass
class DispatchRecord:
    id: str
    transformer_id: str
    idempotency_key: str
    proposed: ProposedAction
    reason: str
    state: DispatchState = DispatchState.PROPOSED
    shield: ShieldDecision | None = None
    final: ProposedAction | None = None
    approved_by: str | None = None
    signed_command: dict | None = None
    measured_battery_kw: float | None = None
    history: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    explanation: dict | None = None

    def to_dict(self) -> dict:
        p = self.proposed
        return {
            "id": self.id,
            "transformer_id": self.transformer_id,
            "state": self.state.value,
            "reason": self.reason,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat(),
            "proposed": {
                "battery_kw": p.battery_kw,
                "flex_shift_kw": p.flex_shift_kw,
                "load_limits": len(p.load_limits_kw),
                "source": p.source,
                "duration_s": p.duration_s,
                "valid_for_s": p.valid_for_s,
            },
            "shield": self.shield.to_dict() if self.shield else None,
            "approved_by": self.approved_by,
            "measured_battery_kw": self.measured_battery_kw,
            "command": {k: v for k, v in (self.signed_command or {}).items() if k != "sig"} or None,
            "history": self.history,
            "explanation": self.explanation,
        }


class EdgeTransport(Protocol):
    async def send(self, transformer_id: str, signed_command: dict) -> bool: ...


class DispatchOrchestrator:
    def __init__(
        self,
        shield: SafetyShield,
        bus: EventBus,
        ledger: AuditLedger,
        signer: CommandSigner,
        transport: EdgeTransport | None = None,
        auto_approve_max_kw: float = 60.0,
        verify_tolerance_kw: float = 3.0,
    ) -> None:
        self.shield, self.bus, self.ledger, self.signer = shield, bus, ledger, signer
        self.transport = transport
        self.auto_approve_max_kw = auto_approve_max_kw
        self.tol = verify_tolerance_kw
        self.records: dict[str, DispatchRecord] = {}
        self.sink = None  # optional async callback(record) for durable persistence
        self._by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- state machine ----
    async def _transition(
        self, rec: DispatchRecord, to: DispatchState, actor: str, role: str, reason: str = "", **payload
    ) -> None:
        if to not in TRANSITIONS.get(rec.state, set()):
            raise IllegalTransition(f"{rec.state} -> {to}")
        frm, rec.state = rec.state, to
        entry = {
            "from": frm.value,
            "to": to.value,
            "at": datetime.now(UTC).isoformat(),
            "actor": actor,
            "reason": reason,
        }
        rec.history.append(entry)
        DISPATCH_TRANSITIONS.labels(to.value).inc()
        await self.ledger.append(
            actor, role, f"dispatch.{to.value.lower()}", "dispatch", rec.id, reason, payload
        )
        if self.sink is not None:
            await self.sink(rec)
        topic = TOPIC_OF.get(to)
        if topic:
            await self.bus.publish(
                CloudEvent.of(topic, "jyotiveda/dispatch", {"dispatch": rec.to_dict()}, rec.transformer_id)
            )

    # ---------------------------------------------------------------- API ----------------
    async def propose(
        self,
        transformer_id: str,
        action: ProposedAction,
        state: GridState,
        reason: str,
        principal: Principal,
        idempotency_key: str | None = None,
        explanation: dict | None = None,
    ) -> DispatchRecord:
        if not principal.can(Perm.PROPOSE_DISPATCH):
            raise PermissionError("not allowed to propose dispatch")
        key = idempotency_key or action.action_id
        async with self._lock:
            if key in self._by_key:  # idempotent retries return the original record
                return self.records[self._by_key[key]]
            rec = DispatchRecord(
                id=f"DSP-{uuid.uuid4().hex[:12]}",
                transformer_id=transformer_id,
                idempotency_key=key,
                proposed=action,
                reason=reason,
                explanation=explanation,
            )
            self.records[rec.id] = rec
            self._by_key[key] = rec.id
        rec.history.append(
            {"to": "PROPOSED", "at": rec.created_at.isoformat(), "actor": principal.sub, "reason": reason}
        )
        DISPATCH_TRANSITIONS.labels("PROPOSED").inc()
        await self.ledger.append(
            principal.sub,
            principal.role.value,
            "dispatch.proposed",
            "dispatch",
            rec.id,
            reason,
            {"battery_kw": action.battery_kw, "source": action.source},
        )
        await self.bus.publish(
            CloudEvent.of(
                Topic.DISPATCH_PROPOSED, "jyotiveda/dispatch", {"dispatch": rec.to_dict()}, transformer_id
            )
        )
        if self.sink is not None:
            await self.sink(rec)
        await self._validate(rec, state, principal)
        return rec

    async def _validate(self, rec: DispatchRecord, state: GridState, principal: Principal) -> None:
        await self._transition(
            rec, DispatchState.VALIDATING, "safety-shield", "SERVICE", "deterministic constraint check"
        )
        dec = self.shield.validate(rec.proposed, state)
        rec.shield = dec
        SHIELD_VERDICTS.labels(dec.verdict.value).inc()
        for v in dec.violations:
            SHIELD_VIOLATIONS.labels(v["rule"]).inc()
        if dec.verdict == Verdict.REJECTED or dec.action is None:
            await self._transition(
                rec,
                DispatchState.REJECTED,
                "safety-shield",
                "SERVICE",
                "shield rejected",
                violations=dec.violations,
            )
            return
        rec.final = dec.action
        big = abs(dec.action.battery_kw) > self.auto_approve_max_kw or len(dec.action.load_limits_kw) > 50
        if big and dec.action.source != "operator":
            await self._transition(
                rec,
                DispatchState.AWAITING_APPROVAL,
                "policy",
                "SERVICE",
                f"|P| > {self.auto_approve_max_kw} kW or wide curtailment: human approval required",
            )
        else:
            await self._transition(
                rec,
                DispatchState.APPROVED,
                "policy",
                "SERVICE",
                "within auto-approval envelope",
                verdict=dec.verdict.value,
            )
            await self._send(rec)

    async def approve(self, dispatch_id: str, principal: Principal, note: str = "") -> DispatchRecord:
        if not principal.can(Perm.APPROVE_DISPATCH):
            raise PermissionError("not allowed to approve dispatch")
        rec = self.records[dispatch_id]
        if not principal.in_scope(rec.transformer_id):
            raise PermissionError("transformer out of scope")
        if datetime.now(UTC) > rec.proposed.issued_at + timedelta(seconds=rec.proposed.valid_for_s):
            await self._transition(
                rec, DispatchState.EXPIRED, principal.sub, principal.role.value, "approval too late"
            )
            return rec
        rec.approved_by = principal.sub
        await self._transition(
            rec, DispatchState.APPROVED, principal.sub, principal.role.value, note or "operator approval"
        )
        await self._send(rec)
        return rec

    async def reject(self, dispatch_id: str, principal: Principal, note: str) -> DispatchRecord:
        if not principal.can(Perm.APPROVE_DISPATCH):
            raise PermissionError("not allowed")
        rec = self.records[dispatch_id]
        await self._transition(rec, DispatchState.REJECTED, principal.sub, principal.role.value, note)
        return rec

    async def _send(self, rec: DispatchRecord) -> None:
        a = rec.final
        assert a is not None
        now = datetime.now(UTC)
        cmd = {
            "command_id": rec.id,
            "transformer_id": rec.transformer_id,
            "battery_kw": round(a.battery_kw, 3),
            "flex_shift_kw": round(a.flex_shift_kw, 3),
            "load_limits_kw": {k: round(v, 3) for k, v in a.load_limits_kw.items()},
            "not_before": now.isoformat(),
            "expires_at": (now + timedelta(seconds=min(a.valid_for_s, 1800))).isoformat(),
            "duration_s": a.duration_s,
        }
        rec.signed_command = self.signer.sign(cmd)
        await self._transition(
            rec,
            DispatchState.SENT,
            "dispatch-orchestrator",
            "SERVICE",
            "signed Ed25519 command sent to edge",
            kid=rec.signed_command["kid"],
        )
        if self.transport is not None:
            ok = await self.transport.send(rec.transformer_id, rec.signed_command)
            if not ok:
                await self._transition(
                    rec, DispatchState.FAILED, "edge", "SERVICE", "edge rejected or unreachable"
                )

    async def on_edge_ack(self, dispatch_id: str, accepted: bool, detail: str = "") -> None:
        rec = self.records[dispatch_id]
        if accepted:
            await self._transition(
                rec, DispatchState.ACKNOWLEDGED, "edge-gateway", "SERVICE", detail or "ack"
            )
        else:
            await self._transition(rec, DispatchState.FAILED, "edge-gateway", "SERVICE", detail or "nack")

    async def on_executed(self, dispatch_id: str, measured_battery_kw: float) -> DispatchRecord:
        rec = self.records[dispatch_id]
        rec.measured_battery_kw = measured_battery_kw
        await self._transition(
            rec,
            DispatchState.EXECUTED,
            "edge-gateway",
            "SERVICE",
            "setpoint applied",
            measured_kw=measured_battery_kw,
        )
        target = rec.final.battery_kw if rec.final else 0.0
        if abs(measured_battery_kw - target) <= max(self.tol, 0.1 * abs(target)):
            await self._transition(
                rec, DispatchState.VERIFIED, "verifier", "SERVICE", "telemetry matches setpoint"
            )
        else:
            await self._transition(
                rec,
                DispatchState.FAILED,
                "verifier",
                "SERVICE",
                f"telemetry {measured_battery_kw:.1f} kW ≠ setpoint {target:.1f} kW",
            )
        return rec

    def list(self, transformer_id: str | None = None, limit: int = 100) -> list[DispatchRecord]:
        rs = [
            r for r in self.records.values() if transformer_id is None or r.transformer_id == transformer_id
        ]
        return sorted(rs, key=lambda r: r.created_at, reverse=True)[:limit]
