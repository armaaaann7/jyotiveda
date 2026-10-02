"""Graph construction for grid intelligence: feeder → DT → LV bus → household.

Node features (per node, standardised):
  [kind one-hot(3), p90 demand kW, p90 solar kW, rating/thermal limit, electrical distance to DT,
   protected (T0+T1) kW, battery kW nearby, historical v_min, loading ratio]
Edges are undirected lines/service drops with resistance and reactance as edge attributes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jyotiveda.domain import Neighbourhood

KINDS = ("transformer", "bus", "household")


@dataclass
class GridGraph:
    node_ids: list[str]
    kind: np.ndarray  # (N,) int index into KINDS
    x: np.ndarray  # (N, F) node features
    edge_index: np.ndarray  # (2, E)
    edge_attr: np.ndarray  # (E, 2) [r_ohm, x_ohm]
    household_rows: np.ndarray  # rows of household nodes in node order
    bus_of_row: np.ndarray  # logical LV bus per node (-1 for DT)

    @property
    def num_nodes(self) -> int:
        return len(self.node_ids)


def build_graph(
    nb: Neighbourhood,
    demand_p90_kw: np.ndarray,  # (H,)
    solar_p90_kw: np.ndarray,  # (H,)
    protected_kw: np.ndarray,  # (H,)
    laterals: int = 3,
    buses_per_lateral: int = 10,
    span_r_ohm: float = 0.0128,  # 40 m of 0.32 ohm/km
    span_x_ohm: float = 0.0032,
) -> GridGraph:
    ids = ["DT"]
    kind = [0]
    bus_of = [-1]
    for b in range(1, laterals * buses_per_lateral + 1):
        ids.append(f"B{b}")
        kind.append(1)
        bus_of.append(b)
    row_of_bus = {b: i for i, b in enumerate(bus_of) if b > 0}
    hh_rows = []
    for h in nb.households:
        hh_rows.append(len(ids))
        ids.append(h.id)
        kind.append(2)
        bus_of.append(h.bus)
    src, dst, attr = [], [], []
    for lat in range(laterals):
        prev = 0
        for k in range(buses_per_lateral):
            b = 1 + lat * buses_per_lateral + k
            src.append(prev)
            dst.append(row_of_bus[b])
            attr.append((span_r_ohm, span_x_ohm))
            prev = row_of_bus[b]
    for r, h in zip(hh_rows, nb.households, strict=True):
        src.append(row_of_bus.get(h.bus, 1))
        dst.append(r)
        attr.append((0.02, 0.004))
    ei = np.array([src + dst, dst + src])
    ea = np.array(attr + attr)
    n = len(ids)
    x = np.zeros((n, 10))
    kind_arr = np.array(kind)
    x[np.arange(n), kind_arr] = 1.0
    hh_rows_a = np.array(hh_rows)
    x[hh_rows_a, 3] = demand_p90_kw
    x[hh_rows_a, 4] = solar_p90_kw
    x[hh_rows_a, 7] = protected_kw
    # aggregate household quantities up to buses and DT
    for r, h in zip(hh_rows, nb.households, strict=True):
        br = row_of_bus.get(h.bus, 1)
        x[br, 3] += x[r, 3]
        x[br, 4] += x[r, 4]
        x[br, 7] += x[r, 7]
    x[0, 3], x[0, 4], x[0, 7] = demand_p90_kw.sum(), solar_p90_kw.sum(), protected_kw.sum()
    x[0, 5] = nb.transformer.rating_kw
    x[0, 8] = nb.battery.max_discharge_kw if nb.battery else 0.0
    # electrical distance (position along lateral)
    for b, r in row_of_bus.items():
        x[r, 6] = ((b - 1) % buses_per_lateral + 1) * span_r_ohm
    for r, h in zip(hh_rows, nb.households, strict=True):
        x[r, 6] = x[row_of_bus.get(h.bus, 1), 6] + 0.02
    x[:, 9] = np.where(x[:, 5] > 0, x[:, 3] / np.maximum(x[:, 5], 1e-6), 0.0)
    return GridGraph(ids, kind_arr, x, ei, ea, hh_rows_a, np.array(bus_of))
