"""Ed25519 command signing — the edge gateway executes only commands signed by the dispatch
orchestrator's key, within their validity window, and never twice (nonce replay cache)."""

from __future__ import annotations

import base64
import secrets
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from jyotiveda.audit.ledger import canonical


class CommandSigner:
    def __init__(self, key: Ed25519PrivateKey, key_id: str = "dispatch-2026") -> None:
        self._key = key
        self.key_id = key_id

    @classmethod
    def from_path_or_ephemeral(cls, path: str | None) -> CommandSigner:
        if path and Path(path).exists():
            key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
            assert isinstance(key, Ed25519PrivateKey)
            return cls(key)
        return cls(Ed25519PrivateKey.generate(), key_id="ephemeral-dev")

    def public_pem(self) -> bytes:
        return self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def sign(self, command: dict) -> dict:
        body = {**command, "nonce": command.get("nonce") or secrets.token_hex(12), "kid": self.key_id}
        sig = self._key.sign(canonical(body))
        return {**body, "sig": base64.b64encode(sig).decode()}


class CommandVerifier:
    def __init__(self, public_pem: bytes, replay_cache: int = 10_000) -> None:
        key = serialization.load_pem_public_key(public_pem)
        assert isinstance(key, Ed25519PublicKey)
        self._pub = key
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max = replay_cache

    def verify(self, signed: dict, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now(UTC)
        body = {k: v for k, v in signed.items() if k != "sig"}
        try:
            self._pub.verify(base64.b64decode(signed["sig"]), canonical(body))
        except Exception:
            return False, "bad signature"
        nb = datetime.fromisoformat(body["not_before"])
        exp = datetime.fromisoformat(body["expires_at"])
        if not (nb.timestamp() - 5 <= now.timestamp() <= exp.timestamp()):
            return False, "outside validity window"
        if body["nonce"] in self._seen:
            return False, "replayed nonce"
        self._seen[body["nonce"]] = None
        if len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return True, "ok"
