"""grid-intelligence-service: node-level stress / voltage / overload risk.

Two engines, same output contract:

* PhysicsRiskEngine — LinDistFlow voltage-drop along each lateral + thermal loading ratio.
  Deterministic, explainable, always available (the fallback and the GNN's teacher).
* GNNRiskEngine — GraphSAGE/GAT surrogate (PyTorch Geometric) trained on thousands of pandapower
  power-flow snapshots (ml/train_gnn.py). Scores a whole DISCOM circle (10^4 DTs) in milliseconds,
  captures cross-lateral interactions and learns from field outcomes (DT failures, complaints).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog

from jyotiveda.gridintel.graph import GridGraph
from jyotiveda.observability import FALLBACK_ACTIVATIONS

log = structlog.get_logger(__name__)


@dataclass
class RiskReport:
    engine: str
    transformer_risk: float  # 0..1
    overload_risk: float
    voltage_risk: float
    critical_node_risk: float
    v_min_estimate_pu: float
    node_risk: dict[str, float]
    top_nodes: list[dict]

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "transformer_risk": round(self.transformer_risk, 3),
            "overload_risk": round(self.overload_risk, 3),
            "voltage_risk": round(self.voltage_risk, 3),
            "critical_node_risk": round(self.critical_node_risk, 3),
            "v_min_estimate_pu": round(self.v_min_estimate_pu, 4),
            "level": level(self.transformer_risk),
            "top_nodes": self.top_nodes,
        }


def level(r: float) -> str:
    return "CRITICAL" if r >= 0.75 else "HIGH" if r >= 0.5 else "MODERATE" if r >= 0.25 else "LOW"


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1 / (1 + np.exp(-x))


class PhysicsRiskEngine:
    name = "physics-lindistflow"

    def __init__(self, v_nom_kv: float = 0.433, pf: float = 0.95) -> None:
        self.v2 = (v_nom_kv * 1000) ** 2
        self.tanphi = float(np.tan(np.arccos(pf)))

    def score(self, g: GridGraph, supply_cap_kw: float | None = None) -> RiskReport:
        x = g.x
        n = g.num_nodes
        net_kw = np.maximum(x[:, 3] - x[:, 4], 0.0)
        # LinDistFlow: ΔV_k ≈ Σ_{lines upstream of k} (R·P_down + X·Q_down) / V² (per-unit, 3-phase balanced)
        # build parent map from edge list (tree)
        parent = -np.ones(n, dtype=int)
        r_up = np.zeros(n)
        x_up = np.zeros(n)
        half = g.edge_index.shape[1] // 2
        for e in range(half):
            a, b = g.edge_index[0, e], g.edge_index[1, e]
            parent[b] = a
            r_up[b], x_up[b] = g.edge_attr[e]
        bus_rows = np.nonzero(g.kind == 1)[0]
        down = np.zeros(n)
        down[bus_rows] = net_kw[bus_rows]  # bus rows already aggregate their households
        # accumulate downstream bus power (process deepest first)
        depth = np.zeros(n, dtype=int)
        for i in range(n):
            j, d = i, 0
            while parent[j] >= 0:
                j, d = parent[j], d + 1
            depth[i] = d
        acc = down.copy()
        for i in sorted(bus_rows, key=lambda k: -depth[k]):
            p = parent[i]
            if p > 0 and g.kind[p] == 1:
                acc[p] += acc[i]
        dv = np.zeros(n)
        for i in sorted(range(n), key=lambda k: depth[k]):
            p = parent[i]
            if p < 0:
                continue
            p_w = (acc[i] if g.kind[i] == 1 else net_kw[i]) * 1000
            dv[i] = dv[p] + (r_up[i] * p_w + x_up[i] * p_w * self.tanphi) / self.v2
        v_est = 1.0 - dv - 0.02 * x[0, 9]  # + transformer drop ≈ 2% at full load
        v_min = float(v_est[g.kind > 0].min())
        loading = x[0, 3] / max(x[0, 5], 1e-6)
        cap = supply_cap_kw if supply_cap_kw is not None else x[0, 5]
        scarcity = max(x[0, 3] - x[0, 4] - cap, 0.0) / max(x[0, 3], 1e-6)
        overload = float(_sigmoid((loading - 0.9) * 12))
        voltage = float(_sigmoid((0.955 - v_min) * 150))
        node = np.asarray(_sigmoid((0.955 - v_est) * 150))
        crit = float(np.max(node[g.household_rows] * (x[g.household_rows, 7] > 0.3), initial=0.0))
        tr = float(1 - (1 - overload) * (1 - voltage) * (1 - min(scarcity * 2, 1)))
        order = np.argsort(-node)[:5]
        return RiskReport(
            self.name,
            tr,
            overload,
            voltage,
            crit,
            v_min,
            {g.node_ids[i]: float(node[i]) for i in range(n)},
            [
                {
                    "node": g.node_ids[i],
                    "risk": round(float(node[i]), 3),
                    "v_est_pu": round(float(v_est[i]), 4),
                }
                for i in order
            ],
        )


class GNNRiskEngine:  # pragma: no cover - requires torch + torch_geometric + checkpoint
    name = "gnn-graphsage"

    def __init__(self, checkpoint: str) -> None:
        import torch

        from jyotiveda.gridintel.gnn import GridRiskGNN

        self.torch = torch
        self.model = GridRiskGNN(in_dim=10, hidden=64)
        self.model.load_state_dict(torch.load(checkpoint, map_location="cpu"))
        self.model.eval()

    def score(self, g: GridGraph, supply_cap_kw: float | None = None) -> RiskReport:
        t = self.torch
        with t.no_grad():
            out = self.model(t.tensor(g.x, dtype=t.float32), t.tensor(g.edge_index, dtype=t.long))
        node = out[:, 0].sigmoid().numpy()
        v = 1.0 - out[:, 1].numpy() * 0.1
        order = np.argsort(-node)[:5]
        return RiskReport(
            self.name,
            float(node[0]),
            float(out[0, 2].sigmoid()),
            float(node[g.kind > 0].max()),
            float(node[g.household_rows].max()),
            float(v[g.kind > 0].min()),
            {g.node_ids[i]: float(node[i]) for i in range(g.num_nodes)},
            [{"node": g.node_ids[i], "risk": round(float(node[i]), 3)} for i in order],
        )


def build_risk_engine(checkpoint: str | None):
    if checkpoint:
        try:
            return GNNRiskEngine(checkpoint)
        except Exception as exc:  # pragma: no cover
            FALLBACK_ACTIVATIONS.labels("gnn").inc()
            log.warning("gnn_unavailable_using_physics", error=str(exc))
    return PhysicsRiskEngine()
