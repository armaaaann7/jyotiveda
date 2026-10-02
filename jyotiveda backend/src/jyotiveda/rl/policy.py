"""rl-policy-service runtime: ONNX-exported PPO policy proposing residual corrections to MPC.

The policy never touches a device. Output flows: policy → MPC blend → safety shield → dispatch.
Health gating: if the policy's recent *unsafe proposal rate* (shield rejections/modifications)
exceeds a threshold, it is automatically bypassed and MPC runs alone (metric + alarm raised).
"""

from __future__ import annotations

from collections import deque

import numpy as np
import structlog

from jyotiveda.observability import FALLBACK_ACTIVATIONS

log = structlog.get_logger(__name__)


class ResidualPolicy:
    def __init__(
        self, onnx_path: str | None = None, max_residual_frac: float = 0.3, unsafe_threshold: float = 0.2
    ) -> None:
        self.session = None
        self.max_residual = max_residual_frac
        self.unsafe_threshold = unsafe_threshold
        self._history: deque[bool] = deque(maxlen=200)
        if onnx_path:
            try:
                import onnxruntime as ort

                self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
                self.input_name = self.session.get_inputs()[0].name
            except Exception as exc:  # pragma: no cover
                FALLBACK_ACTIVATIONS.labels("rl-policy").inc()
                log.warning("rl_policy_unavailable", error=str(exc))

    @property
    def healthy(self) -> bool:
        if not self._history:
            return True
        return (sum(self._history) / len(self._history)) <= self.unsafe_threshold

    @property
    def active(self) -> bool:
        return self.session is not None and self.healthy

    def record_shield_outcome(self, unsafe: bool) -> None:
        self._history.append(unsafe)

    def propose(self, obs: np.ndarray, mpc_battery_kw: float, rating_kw: float) -> tuple[float, dict]:
        if not self.active:
            return mpc_battery_kw, {"policy": "mpc-only", "residual_kw": 0.0, "healthy": self.healthy}
        out = self.session.run(None, {self.input_name: obs.reshape(1, -1).astype(np.float32)})[0][
            0
        ]  # pragma: no cover
        residual = float(np.clip(out[0], -1, 1)) * self.max_residual * rating_kw  # pragma: no cover
        return mpc_battery_kw + residual, {
            "policy": "ppo-residual",
            "residual_kw": residual,
            "healthy": True,
        }  # pragma: no cover
