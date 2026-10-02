"""Fleet registry + per-transformer runtime (live twin state, cached forecasts, edge gateway)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from jyotiveda.config import Settings
from jyotiveda.edge.gateway import EdgeGateway
from jyotiveda.edge.protocols import VirtualMeterBank, VirtualPCS
from jyotiveda.fairness.debt import FairnessLedger
from jyotiveda.forecasting.service import ForecastService, GapForecast
from jyotiveda.safety.shield import SafetyShield
from jyotiveda.security.signing import CommandVerifier
from jyotiveda.twin.neighbourhood import build_neighbourhood
from jyotiveda.twin.scenario import ScenarioSpec, SupplyWindow
from jyotiveda.twin.simulator import TwinSimulator

IST = ZoneInfo("Asia/Kolkata")

# Reference fleet: one DISCOM sub-division (illustrative wards, peri-urban Pune)
FLEET_SPEC = [
    ("DT-101", "Sai Nagar", 160, 0.30, 150, 250, 75),
    ("DT-102", "Ramtekdi", 220, 0.15, 0, 250, 80),
    ("DT-103", "Mundhwa Gaothan", 140, 0.45, 200, 200, 70),
    ("DT-104", "Hadapsar Gadital", 180, 0.33, 200, 250, 70),
    ("DT-105", "Kondhwa Budruk", 240, 0.20, 100, 315, 110),
    ("DT-106", "Wanowrie Bazaar", 120, 0.10, 0, 160, 55),
    ("DT-107", "Undri Chowk", 200, 0.40, 250, 250, 60),
    ("DT-108", "Fursungi Phata", 260, 0.25, 150, 400, 150),
]


@dataclass
class TransformerRuntime:
    spec: ScenarioSpec
    ward: str
    settings: Settings
    verifier_pem: bytes
    rating_kva: float = 250.0
    solar_share: float = 0.33
    sim: TwinSimulator = field(init=False)
    gateway: EdgeGateway = field(init=False)
    ledger: FairnessLedger = field(init=False)
    clock_override: datetime | None = None
    _fc: dict | None = None

    def __post_init__(self) -> None:
        nb = build_neighbourhood(
            self.spec.transformer_id,
            n_connections=self.spec.n_connections,
            solar_share=self.solar_share,
            battery_kwh=self.spec.battery_kwh,
            battery_kw=self.spec.battery_kw,
            rating_kva=self.rating_kva,
            seed=self.spec.seed,
        )
        self.sim = TwinSimulator(self.spec, self.settings, nb=nb)
        self.ledger = FairnessLedger.for_households(nb.households)
        self.limits = self.sim.limits
        b = nb.battery
        self.gateway = EdgeGateway(
            transformer_id=nb.transformer.id,
            verifier=CommandVerifier(self.verifier_pem),
            shield=SafetyShield(self.limits),
            pcs=VirtualPCS(capacity_kwh=b.capacity_kwh if b else 1e-6, soc=self.spec.soc0),
            meters=VirtualMeterBank(),
            lifeline_kw_by_household=self.sim.lifeline_floor,
        )

    @property
    def id(self) -> str:
        return self.sim.nb.transformer.id

    def now(self) -> datetime:
        return self.clock_override or datetime.now(UTC)

    def slot(self, now: datetime | None = None) -> int:
        local = (now or self.now()).astimezone(IST) if (now or self.now()).tzinfo else (now or self.now())
        return min((local.hour * 60 + local.minute) // self.settings.slot_minutes, self.sim.T - 1)

    def set_clock(self, hour: float) -> None:
        day = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        self.clock_override = (day + timedelta(hours=hour)).astimezone(UTC)

    def forecasts(self) -> dict:
        if self._fc is None:
            dem, sol, shares, meta = self.sim._forecasts()
            cap = self.sim._grid_cap_forecast(sol)
            gap: GapForecast = ForecastService.gap(dem, sol, cap, seed=self.spec.seed)
            self._fc = {"demand": dem, "solar": sol, "shares": shares, "meta": meta, "cap": cap, "gap": gap}
        return self._fc

    def live_state(self, now: datetime | None = None) -> dict:
        t = self.slot(now)
        s = self.sim
        demand = float(s.demand.total[t].sum())
        solar = float(s.solar[t].sum())
        cap = float(s.grid_cap[t])
        pcs: VirtualPCS = self.gateway.pcs  # type: ignore[assignment]
        net = demand - solar - pcs.power_kw
        return {
            "transformer_id": self.id,
            "ward": self.ward,
            "slot": t,
            "ts": s.index[t].isoformat(),
            "demand_kw": round(demand, 2),
            "solar_kw": round(solar, 2),
            "grid_cap_kw": round(cap, 2),
            "net_import_kw": round(max(net, 0), 2),
            "loading_pct": round(max(net, 0) / s.nb.transformer.rating_kw * 100, 1),
            "protected_kw": round(float(s.demand.protected[t].sum()), 2),
            "battery": {
                "soc": round(pcs.soc, 4),
                "power_kw": round(pcs.power_kw, 2),
                "temperature_c": pcs.temperature_c,
                "capacity_kwh": s.nb.battery.capacity_kwh if s.nb.battery else 0,
            },
            "edge_mode": self.gateway.mode.value,
            "renewable_share": round(solar / max(demand, 1e-6), 3),
            "shortfall_kw": round(max(demand - solar - cap, 0), 2),
        }


def build_fleet(settings: Settings, verifier_pem: bytes) -> dict[str, TransformerRuntime]:
    out = {}
    for i, (tid, ward, n, solar, batt, kva, cap) in enumerate(FLEET_SPEC):
        if settings.fleet_shard and tid not in settings.fleet_shard:
            continue  # cell-based architecture: each cell owns a shard of transformers
        spec = ScenarioSpec(
            transformer_id=tid,
            n_connections=n,
            battery_kwh=batt,
            battery_kw=min(batt / 2, 150) if batt else 0,
            solar_reduction=0.5 + 0.05 * (i % 4),
            supply_windows=[SupplyWindow(start_hour=17.5, end_hour=22.5, cap_kw=cap)],
            seed=100 + i,
            run_power_flow=False,
        )
        out[tid] = TransformerRuntime(
            spec=spec,
            ward=ward,
            settings=settings,
            verifier_pem=verifier_pem,
            rating_kva=kva,
            solar_share=solar,
        )
    return out


def peak_index(arr: np.ndarray, start: int, width: int = 16) -> int:
    seg = arr[start : start + width]
    return start + int(np.argmax(seg)) if seg.size else start
