"""Field protocol adapters (edge side).

* DLMS/COSEM (IS 15959 Indian smart-meter companion standard) — OBIS code map for the values
  Jyotiveda uses; production uses the Gurux DLMS stack over the meter HES / optical / RF-mesh path.
* Modbus / SunSpec — rooftop inverters and battery PCS (SunSpec models 103, 124, 802).
* Virtual devices — twin-backed drivers so the whole edge stack runs in CI and demos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# IS 15959 / IEC 62056 OBIS codes (instantaneous profile)
OBIS = {
    "voltage_r": "1.0.32.7.0.255",
    "voltage_y": "1.0.52.7.0.255",
    "voltage_b": "1.0.72.7.0.255",
    "current_r": "1.0.31.7.0.255",
    "active_power_import_w": "1.0.1.7.0.255",
    "active_power_export_w": "1.0.2.7.0.255",
    "power_factor": "1.0.13.7.0.255",
    "frequency_hz": "1.0.14.7.0.255",
    "energy_import_wh": "1.0.1.8.0.255",
    "energy_export_wh": "1.0.2.8.0.255",
    "load_limit_w": "0.0.17.0.0.255",  # limiter object: used for lifeline-mode load limiting
    "relay_status": "0.0.96.3.10.255",  # disconnect control
}

# SunSpec register models used for PCS / inverter control
SUNSPEC_MODELS = {"inverter_3ph": 103, "immediate_controls": 123, "storage": 124, "battery_bank": 802}


class MeterDriver(Protocol):
    async def read(self, meter_id: str) -> dict[str, float]: ...
    async def set_load_limit(self, meter_id: str, watts: float) -> bool: ...


class PCSDriver(Protocol):
    async def set_power(self, kw: float) -> bool: ...
    async def read(self) -> dict[str, float]: ...


@dataclass
class VirtualPCS:
    """Battery PCS + BMS emulator (SoC integration, temperature drift, alarms injectable)."""

    capacity_kwh: float = 200.0
    soc: float = 0.5
    temperature_c: float = 31.0
    power_kw: float = 0.0
    alarms: list[str] = field(default_factory=list)
    fail_next: bool = False

    async def set_power(self, kw: float) -> bool:
        if self.fail_next:
            self.fail_next = False
            return False
        self.power_kw = kw
        return True

    def advance(self, seconds: float) -> None:
        h = seconds / 3600
        eff = 0.96
        de = -self.power_kw * h / eff if self.power_kw > 0 else -self.power_kw * h * eff
        self.soc = min(max(self.soc + de / self.capacity_kwh, 0.0), 1.0)

    async def read(self) -> dict[str, float]:
        return {"soc": self.soc, "temperature_c": self.temperature_c, "power_kw": self.power_kw}


@dataclass
class VirtualMeterBank:
    limits_w: dict[str, float] = field(default_factory=dict)

    async def read(self, meter_id: str) -> dict[str, float]:
        return {
            "active_power_import_w": 400.0,
            "voltage_r": 236.0,
            "load_limit_w": self.limits_w.get(meter_id, 0.0),
        }

    async def set_load_limit(self, meter_id: str, watts: float) -> bool:
        self.limits_w[meter_id] = watts
        return True
