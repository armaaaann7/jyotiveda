"""pandapower model of one distribution-transformer neighbourhood.

11 kV external grid -> 250 kVA 11/0.433 kV DT -> LV busbar -> N laterals of M buses each.
Households are aggregated per LV bus for power flow (loads + static generators), which keeps
AC power flow at ~10-20 ms per snapshot while preserving voltage drop along each lateral.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jyotiveda.domain import Neighbourhood


@dataclass
class PowerFlowResult:
    converged: bool
    v_min_pu: float
    v_max_pu: float
    trafo_loading_pct: float
    losses_kw: float
    bus_v_pu: dict[int, float]
    line_loading_pct: dict[int, float]


class FeederModel:
    V_BAND = (0.94, 1.06)  # +/-6% LV statutory band

    def __init__(self, nb: Neighbourhood, laterals: int = 3, buses_per_lateral: int = 10) -> None:
        import pandapower as pp

        self.nb = nb
        self.pp = pp
        net = pp.create_empty_network(name=nb.transformer.id, f_hz=50.0)
        mv = pp.create_bus(net, vn_kv=11.0, name="MV")
        lv0 = pp.create_bus(net, vn_kv=0.433, name="LV-busbar")
        pp.create_ext_grid(net, mv, vm_pu=1.0, name="Grid")
        pp.create_transformer_from_parameters(
            net,
            hv_bus=mv,
            lv_bus=lv0,
            sn_mva=nb.transformer.rating_kva / 1000,
            vn_hv_kv=11.0,
            vn_lv_kv=0.433,
            vk_percent=4.5,
            vkr_percent=1.2,
            pfe_kw=0.6,
            i0_percent=0.3,
            name=nb.transformer.id,
        )
        self.bus_of: dict[int, int] = {0: lv0}
        for lat in range(laterals):
            prev = lv0
            for k in range(buses_per_lateral):
                logical = 1 + lat * buses_per_lateral + k
                b = pp.create_bus(net, vn_kv=0.433, name=f"L{lat}-B{k}")
                # 3.5C 95 mm2 AL XLPE-ish overhead ABC, 40 m spans
                pp.create_line_from_parameters(
                    net,
                    prev,
                    b,
                    length_km=0.04,
                    r_ohm_per_km=0.32,
                    x_ohm_per_km=0.08,
                    c_nf_per_km=0.0,
                    max_i_ka=0.2,
                    name=f"L{lat}-S{k}",
                )
                self.bus_of[logical] = b
                prev = b
        self._load_idx: dict[int, int] = {}
        self._sgen_idx: dict[int, int] = {}
        for logical, b in self.bus_of.items():
            self._load_idx[logical] = pp.create_load(net, b, p_mw=0.0, q_mvar=0.0)
            self._sgen_idx[logical] = pp.create_sgen(net, b, p_mw=0.0, q_mvar=0.0)
        self.batt_idx = pp.create_storage(
            net, lv0, p_mw=0.0, max_e_mwh=(nb.battery.capacity_kwh / 1000) if nb.battery else 0.0
        )
        self.net = net
        self._hh_bus = np.array([min(h.bus, max(self.bus_of)) for h in nb.households])

    def solve(
        self,
        served_kw_by_household: np.ndarray,
        solar_kw_by_household: np.ndarray,
        battery_kw: float = 0.0,
        pf: float = 0.95,
    ) -> PowerFlowResult:
        """battery_kw > 0 = discharging into the LV busbar."""
        net = self.net
        tanphi = float(np.tan(np.arccos(pf)))
        load = np.bincount(self._hh_bus, weights=served_kw_by_household, minlength=max(self.bus_of) + 1)
        gen = np.bincount(self._hh_bus, weights=solar_kw_by_household, minlength=max(self.bus_of) + 1)
        for logical in self.bus_of:
            net.load.at[self._load_idx[logical], "p_mw"] = load[logical] / 1000
            net.load.at[self._load_idx[logical], "q_mvar"] = load[logical] * tanphi / 1000
            net.sgen.at[self._sgen_idx[logical], "p_mw"] = gen[logical] / 1000
        net.storage.at[self.batt_idx, "p_mw"] = -battery_kw / 1000  # pandapower: + = charging (load)
        try:
            self.pp.runpp(net, algorithm="nr", init="auto", numba=False)
        except Exception:
            return PowerFlowResult(False, 0.0, 0.0, 999.0, 0.0, {}, {})
        vm = net.res_bus.vm_pu
        lv = vm.iloc[1:]
        return PowerFlowResult(
            converged=True,
            v_min_pu=float(lv.min()),
            v_max_pu=float(lv.max()),
            trafo_loading_pct=float(net.res_trafo.loading_percent.iloc[0]),
            losses_kw=float(net.res_line.pl_mw.sum() * 1000 + net.res_trafo.pl_mw.sum() * 1000),
            bus_v_pu={logical: float(vm.at[b]) for logical, b in self.bus_of.items()},
            line_loading_pct={int(i): float(v) for i, v in net.res_line.loading_percent.items()},
        )

    def topology(self) -> dict:
        """Graph export for the GNN and the frontend map (nodes + edges)."""
        nodes = [{"id": "DT", "kind": "transformer", "bus": 0}]
        nodes += [{"id": f"B{logical}", "kind": "bus", "bus": logical} for logical in self.bus_of if logical]
        nodes += [{"id": h.id, "kind": "household", "bus": h.bus} for h in self.nb.households]
        edges = []
        for _, row in self.net.line.iterrows():
            a = next(k for k, v in self.bus_of.items() if v == row.from_bus)
            b = next(k for k, v in self.bus_of.items() if v == row.to_bus)
            edges.append({"from": "DT" if a == 0 else f"B{a}", "to": f"B{b}", "kind": "line"})
        edges += [{"from": f"B{h.bus}", "to": h.id, "kind": "service"} for h in self.nb.households]
        return {"nodes": nodes, "edges": edges}
