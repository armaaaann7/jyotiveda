"""Train the GraphSAGE/GATv2 grid-stress surrogate on pandapower-labelled snapshots.

    uv run --extra ml python ml/train_gnn.py --snapshots 20000 --out models/gnn_risk.pt

Teacher: AC power flow on randomised neighbourhoods (size, solar share, loading, time of day).
Labels per node: stress (v < 0.955 pu or line > 90%), voltage drop, DT overload.
Evaluation: precision/recall on stressed nodes, missed-critical-node rate (the metric that matters).
"""

from __future__ import annotations

import argparse

import numpy as np


def make_snapshot(rng: np.random.Generator):
    from jyotiveda.gridintel.graph import build_graph
    from jyotiveda.twin.neighbourhood import build_neighbourhood
    from jyotiveda.twin.network import FeederModel

    nb = build_neighbourhood(
        n_connections=int(rng.integers(80, 260)),
        solar_share=float(rng.uniform(0, 0.6)),
        seed=int(rng.integers(0, 1_000_000)),
    )
    H = len(nb.households)
    demand = rng.gamma(2.0, 0.45, H) * rng.uniform(0.4, 1.6)
    solar = np.array([h.solar_kwp for h in nb.households]) * rng.uniform(0, 0.8)
    prot = np.array([h.lifeline_kw + h.critical_kw for h in nb.households])
    g = build_graph(nb, demand, solar, prot)
    pf = FeederModel(nb).solve(demand, solar)
    v = np.ones(g.num_nodes)
    for i, b in enumerate(g.bus_of_row):
        v[i] = pf.bus_v_pu.get(int(b), 1.0) if b > 0 else 1.0
    y = np.stack(
        [(v < 0.955).astype(float), (1 - v) * 10, np.full(g.num_nodes, float(pf.trafo_loading_pct > 100))], 1
    )
    return g, y


def main() -> None:  # pragma: no cover - requires the `ml` extra
    import mlflow
    import torch
    from torch_geometric.data import Data
    from torch_geometric.loader import DataLoader

    from jyotiveda.gridintel.gnn import GridRiskGNN

    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=int, default=20000)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--out", default="models/gnn_risk.pt")
    args = ap.parse_args()
    rng = np.random.default_rng(0)
    data = []
    for _ in range(args.snapshots):
        g, y = make_snapshot(rng)
        data.append(
            Data(
                x=torch.tensor(g.x, dtype=torch.float32),
                edge_index=torch.tensor(g.edge_index),
                y=torch.tensor(y, dtype=torch.float32),
            )
        )
    split = int(0.9 * len(data))
    train, val = (
        DataLoader(data[:split], batch_size=32, shuffle=True),
        DataLoader(data[split:], batch_size=64),
    )
    model = GridRiskGNN(in_dim=10, hidden=64)
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    bce, mse = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(5.0)), torch.nn.MSELoss()
    mlflow.set_experiment("jyotiveda-gnn")
    with mlflow.start_run(run_name="graphsage-gatv2"):
        for ep in range(args.epochs):
            model.train()
            for batch in train:
                out = model(batch.x, batch.edge_index)
                loss = (
                    bce(out[:, 0], batch.y[:, 0])
                    + mse(out[:, 1], batch.y[:, 1])
                    + 0.2 * bce(out[:, 2], batch.y[:, 2])
                )
                opt.zero_grad()
                loss.backward()
                opt.step()
            model.eval()
            tp = fp = fn = 0
            with torch.no_grad():
                for batch in val:
                    pred = model(batch.x, batch.edge_index)[:, 0].sigmoid() > 0.5
                    true = batch.y[:, 0] > 0.5
                    tp += int((pred & true).sum())
                    fp += int((pred & ~true).sum())
                    fn += int((~pred & true).sum())
            prec, rec = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
            mlflow.log_metrics({"precision": prec, "recall": rec, "missed_critical_rate": 1 - rec}, step=ep)
            print(f"epoch {ep}: precision={prec:.3f} recall={rec:.3f}")
        torch.save(model.state_dict(), args.out)
        mlflow.log_artifact(args.out)


if __name__ == "__main__":
    main()
