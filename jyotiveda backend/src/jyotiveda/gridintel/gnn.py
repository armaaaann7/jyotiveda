"""GraphSAGE + GATv2 hybrid for node-level grid-stress prediction (PyTorch Geometric).

Heads: [node stress logit, voltage-drop (x10%), transformer overload logit].
Trained by ml/train_gnn.py on pandapower-labelled snapshots (teacher = AC power flow),
exported to ONNX for edge inference. Requires the `ml` extra.
"""

from __future__ import annotations

try:  # pragma: no cover - optional heavy dependency
    import torch
    from torch import nn
    from torch_geometric.nn import GATv2Conv, SAGEConv

    class GridRiskGNN(nn.Module):
        def __init__(self, in_dim: int = 10, hidden: int = 64, heads: int = 4) -> None:
            super().__init__()
            self.sage1 = SAGEConv(in_dim, hidden)
            self.gat = GATv2Conv(hidden, hidden // heads, heads=heads)
            self.sage2 = SAGEConv(hidden, hidden)
            self.norm = nn.LayerNorm(hidden)
            self.head = nn.Sequential(nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, 3))

        def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
            h = torch.relu(self.sage1(x, edge_index))
            h = h + torch.relu(self.gat(h, edge_index))  # residual attention over neighbours
            h = self.norm(h + torch.relu(self.sage2(h, edge_index)))
            return self.head(h)

except ImportError:  # pragma: no cover
    GridRiskGNN = None  # type: ignore[assignment,misc]
