"""Scenario specification for the digital twin (what-if, counterfactual, replay)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class SupplyWindow(BaseModel):
    start_hour: float = Field(ge=0, lt=24)
    end_hour: float = Field(gt=0, le=24)
    cap_kw: float = Field(ge=0, description="upstream supply available to this DT in the window")


class ScenarioSpec(BaseModel):
    name: str = "renewable-shock"
    day: date = date(2026, 7, 15)
    transformer_id: str = "DT-104"
    n_connections: int = Field(180, ge=10, le=600)
    solar_reduction: float = Field(0.6, ge=0, le=1, description="cloud-attack depth, 0..1")
    cloud_start_hour: float = 12.0
    cloud_end_hour: float = 18.5
    regional_coupling: float = Field(
        0.5, ge=0, le=1, description="share of the cloud shock that also cuts grid supply"
    )
    heat_index: float = Field(1.0, ge=0.8, le=1.6)
    supply_windows: list[SupplyWindow] = Field(
        default_factory=lambda: [SupplyWindow(start_hour=17.5, end_hour=22.5, cap_kw=70.0)],
        description="DISCOM scarcity / load-shedding windows (renewable ramp-down)",
    )
    battery_kwh: float = Field(200.0, ge=0, le=2000)
    battery_kw: float = Field(100.0, ge=0, le=1000)
    soc0: float = Field(0.5, ge=0.1, le=0.95)
    forecast_error: float = Field(
        0.08, ge=0, le=0.5, description="persistent demand forecast error (1-sigma)"
    )
    replan_every_slots: int = Field(8, ge=1, le=96)
    flex_participation: float = Field(
        0.6, ge=0, le=1, description="share of homes that post flexibility offers"
    )
    run_power_flow: bool = True
    seed: int = 42
