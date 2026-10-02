"""Core domain model. Units: power in kW, energy in kWh, voltage in per-unit (p.u.), money in INR."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Criticality(IntEnum):
    """Lower number = protected first. The safety shield never curtails T0/T1."""

    T0_LIFE_CRITICAL = 0  # medical devices, clinic cold chain, street lights at junctions
    T1_LIFELINE = 1  # light, fan, phone charging, household fridge (~150-300 W per home)
    T2_LIVELIHOOD = 2  # sewing machine, shop fridge, small workshop tools
    T3_FLEXIBLE = 3  # washing machine, water pump, geyser, EV / e-rickshaw charging
    T4_DISCRETIONARY = 4  # AC, TV and other comfort loads


class IncomeBand(StrEnum):
    LOW = "low"
    MIDDLE = "middle"
    COMMERCIAL = "commercial"


class ConnectionType(StrEnum):
    RESIDENTIAL = "residential"
    SHOP = "shop"
    CLINIC = "clinic"
    SCHOOL = "school"


class Household(Frozen):
    id: str
    transformer_id: str
    bus: int = Field(description="LV bus index in the feeder model")
    lateral: int = 0
    connection: ConnectionType = ConnectionType.RESIDENTIAL
    income_band: IncomeBand = IncomeBand.LOW
    sanctioned_kw: float = 2.0
    lifeline_kw: float = 0.25
    critical_kw: float = 0.0  # T0 loads (clinic, medical device)
    livelihood_kw: float = 0.0
    solar_kwp: float = 0.0
    has_ev: bool = False
    lat: float | None = None
    lon: float | None = None


class Battery(Frozen):
    id: str
    transformer_id: str
    capacity_kwh: float = 200.0
    max_charge_kw: float = 60.0
    max_discharge_kw: float = 60.0
    soc_min: float = 0.10
    soc_max: float = 0.95
    eta_charge: float = 0.96
    eta_discharge: float = 0.96
    chemistry: str = "LFP"
    second_life: bool = True
    replacement_cost_inr_per_kwh: float = 8000.0
    rated_cycles_at_80dod: int = 3000


class Transformer(Frozen):
    id: str
    name: str
    feeder_id: str
    rating_kva: float = 250.0
    power_factor: float = 0.95
    emergency_overload_pct: float = 110.0
    v_nominal_kv: float = 0.433
    lat: float | None = None
    lon: float | None = None

    @property
    def rating_kw(self) -> float:
        return self.rating_kva * self.power_factor


class BatteryState(BaseModel):
    battery_id: str
    ts: datetime
    soc: float = Field(ge=0, le=1)
    soh: float = Field(1.0, ge=0, le=1)
    temperature_c: float = 30.0
    power_kw: float = 0.0  # + discharge, - charge
    cycles: float = 0.0
    alarms: list[str] = []


class TransformerState(BaseModel):
    transformer_id: str
    ts: datetime
    load_kw: float
    loading_pct: float
    oil_temp_c: float = 55.0
    v_min_pu: float = 1.0
    v_max_pu: float = 1.0
    phase_imbalance_pct: float = 0.0
    solar_kw: float = 0.0
    grid_import_kw: float = 0.0
    grid_cap_kw: float | None = None
    telemetry_age_s: float = 0.0


class Neighbourhood(BaseModel):
    """Everything behind one distribution transformer — Jyotiveda's unit of deployment."""

    transformer: Transformer
    households: list[Household]
    battery: Battery | None = None

    @property
    def lifeline_kw(self) -> float:
        return sum(h.lifeline_kw + h.critical_kw for h in self.households)

    @property
    def solar_kwp(self) -> float:
        return sum(h.solar_kwp for h in self.households)
