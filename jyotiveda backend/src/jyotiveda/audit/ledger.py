"""Tamper-evident audit ledger (hash chain, append-only).

Every physical-command lifecycle step and every privileged action is recorded as:
    who · what · when · why · approved-by · executed-where · result
record.hash = SHA-256(canonical_json(record without hash) + prev_hash)

Any edit or deletion of a past record breaks `verify()`. The chain head is periodically signed
(Ed25519) and can be anchored externally (e.g. DISCOM's records system), giving regulators an
independently verifiable history of what the AI proposed and what was actually executed.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import orjson

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z)


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: str
    actor: str  # user id / service identity (SPIFFE-style)
    actor_role: str
    action: str  # e.g. dispatch.approved
    entity_type: str
    entity_id: str
    reason: str
    payload: dict
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(body: dict, prev_hash: str) -> str:
    return hashlib.sha256(canonical(body) + prev_hash.encode()).hexdigest()


class AuditLedger:
    def __init__(self, sink=None) -> None:
        self._records: list[AuditRecord] = []
        self._sink = sink  # optional async persistence callback(record)

    @property
    def head(self) -> str:
        return self._records[-1].hash if self._records else GENESIS

    def __len__(self) -> int:
        return len(self._records)

    async def append(
        self,
        actor: str,
        actor_role: str,
        action: str,
        entity_type: str,
        entity_id: str,
        reason: str = "",
        payload: dict | None = None,
    ) -> AuditRecord:
        body = {
            "seq": len(self._records) + 1,
            "ts": datetime.now(UTC).isoformat(),
            "actor": actor,
            "actor_role": actor_role,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": reason,
            "payload": payload or {},
        }
        prev = self.head
        rec = AuditRecord(**body, prev_hash=prev, hash=_digest(body, prev))
        self._records.append(rec)
        if self._sink is not None:
            await self._sink(rec)
        return rec

    def records(self, entity_id: str | None = None, limit: int = 200) -> list[AuditRecord]:
        rs = [r for r in self._records if entity_id is None or r.entity_id == entity_id]
        return rs[-limit:]

    @staticmethod
    def verify(records: list[AuditRecord]) -> tuple[bool, int | None]:
        prev = GENESIS
        for r in records:
            body = {k: v for k, v in r.to_dict().items() if k not in ("hash", "prev_hash")}
            if r.prev_hash != prev or _digest(body, prev) != r.hash:
                return False, r.seq
            prev = r.hash
        return True, None
