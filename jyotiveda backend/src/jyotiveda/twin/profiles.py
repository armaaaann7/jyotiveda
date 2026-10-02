"""Load and solar profiles.

Demand is decomposed per household into criticality tiers (T0..T4) so every downstream layer
(reliability budget, MPC, safety shield) reasons about *what* is being served, not just kW.
Solar uses pvlib's Ineichen clear-sky model scaled by a cloud-attenuation series.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from jyotiveda.domain import ConnectionType, Household, IncomeBand

TIERS = ("t0", "t1", "t2", "t3", "t4")


@dataclass
class TieredDemand:
    """Arrays of shape (T, H) in kW. total = t0 + t1 + t2 + t3 + t4."""

    index: pd.DatetimeIndex
    household_ids: list[str]
    t0: np.ndarray
    t1: np.ndarray
    t2: np.ndarray
    t3: np.ndarray
    t4: np.ndarray

    @property
    def total(self) -> np.ndarray:
        return self.t0 + self.t1 + self.t2 + self.t3 + self.t4

    @property
    def protected(self) -> np.ndarray:
        """T0 + T1: the load the safety shield will never curtail."""
        return self.t0 + self.t1

    def aggregate(self) -> dict[str, np.ndarray]:
        return {k: getattr(self, k).sum(axis=1) for k in TIERS}

    def scaled(self, factor: float | np.ndarray) -> TieredDemand:
        f = np.asarray(factor)
        f = f[:, None] if f.ndim == 1 else f
        return TieredDemand(self.index, self.household_ids, *(getattr(self, k) * f for k in TIERS))


def slot_index(start: datetime, slots: int, slot_minutes: int = 15) -> pd.DatetimeIndex:
    return pd.date_range(start=pd.Timestamp(start), periods=slots, freq=f"{slot_minutes}min")


def _hour_shape(hours: np.ndarray, kind: ConnectionType) -> np.ndarray:
    """Normalised (0..1) diurnal shapes typical of Indian urban / peri-urban consumers."""
    h = hours
    if kind == ConnectionType.SHOP:
        return np.clip(
            0.15 + 0.85 * ((h >= 9) & (h < 22)) * (0.7 + 0.3 * np.exp(-((h - 19.5) ** 2) / 4)), 0, 1
        )
    if kind == ConnectionType.CLINIC:
        return 0.6 + 0.4 * ((h >= 8) & (h < 20))
    if kind == ConnectionType.SCHOOL:
        return 0.05 + 0.95 * ((h >= 8) & (h < 15))
    morning = 0.55 * np.exp(-((h - 7.5) ** 2) / 2.5)
    evening = 1.0 * np.exp(-((h - 20.0) ** 2) / 5.0)
    night = 0.35 * ((h >= 23) | (h < 5))
    midday = 0.25 * np.exp(-((h - 13.5) ** 2) / 6.0)
    return np.clip(0.12 + morning + evening + night + midday, 0, 1.3)


def household_demand(
    households: list[Household],
    index: pd.DatetimeIndex,
    seed: int = 7,
    heat_index: float = 1.0,
) -> TieredDemand:
    """Synthetic but realistic 15-min tiered demand. `heat_index` > 1 models a heatwave (fans/ACs)."""
    rng = np.random.default_rng(seed)
    hours = index.hour.to_numpy() + index.minute.to_numpy() / 60.0
    T, H = len(index), len(households)
    t0, t1, t2, t3, t4 = (np.zeros((T, H)) for _ in range(5))
    for j, hh in enumerate(households):
        shape = _hour_shape(hours, hh.connection)
        noise = np.clip(rng.normal(1.0, 0.12, T), 0.6, 1.5)
        peak = hh.sanctioned_kw * (0.32 if hh.income_band == IncomeBand.LOW else 0.42)
        total = peak * shape * noise * (1 + 0.25 * (heat_index - 1) * (hours >= 11))
        t0[:, j] = hh.critical_kw
        lifeline = np.minimum(total, hh.lifeline_kw * (0.6 + 0.4 * (shape > 0.4)))
        t1[:, j] = lifeline
        rest = np.maximum(total - lifeline, 0)
        liv = np.minimum(rest, hh.livelihood_kw * ((hours >= 9) & (hours < 21)))
        t2[:, j] = liv
        rest = rest - liv
        flex_share = 0.45 if hh.has_ev else 0.3
        t3[:, j] = rest * flex_share
        t4[:, j] = rest * (1 - flex_share) * heat_index
    ids = [h.id for h in households]
    return TieredDemand(index, ids, t0, t1, t2, t3, t4)


def clear_sky_pv(index: pd.DatetimeIndex, lat: float, lon: float, tz: str = "Asia/Kolkata") -> np.ndarray:
    """kW per kWp from pvlib clear-sky GHI with a simple temperature/system derate (PR ~ 0.78)."""
    import pvlib

    loc = pvlib.location.Location(lat, lon, tz=tz)
    idx = index.tz_localize(tz) if index.tz is None else index
    cs = loc.get_clearsky(idx, model="ineichen")
    return np.clip(cs["ghi"].to_numpy() / 1000.0 * 0.78, 0, 1)


def cloud_attenuation(
    index: pd.DatetimeIndex,
    reduction: float = 0.0,
    start_hour: float = 12.0,
    end_hour: float = 18.0,
    seed: int = 11,
) -> np.ndarray:
    """Multiplicative cloud factor in [0,1]: a smooth monsoon cloud bank with stochastic breaks."""
    rng = np.random.default_rng(seed)
    hours = index.hour.to_numpy() + index.minute.to_numpy() / 60.0
    base = np.ones(len(index))
    if reduction <= 0:
        return np.clip(base - np.abs(rng.normal(0, 0.03, len(index))), 0, 1)
    window = (hours >= start_hour) & (hours < end_hour)
    ramp = np.clip(np.minimum(hours - start_hour, end_hour - hours) / 0.75, 0, 1)
    flicker = np.clip(rng.normal(0, 0.08, len(index)), -0.2, 0.2)
    return np.clip(base - window * ramp * (reduction + flicker), 0.02, 1)


def solar_matrix(households: list[Household], pv_per_kwp: np.ndarray) -> np.ndarray:
    kwp = np.array([h.solar_kwp for h in households])
    return pv_per_kwp[:, None] * kwp[None, :]


def tod_tariff(index: pd.DatetimeIndex) -> np.ndarray:
    """Time-of-day tariff (INR/kWh): solar hours -20%, evening peak +20% (CEA consumer-rules pattern)."""
    base = 6.5
    h = index.hour.to_numpy()
    return np.where((h >= 9) & (h < 17), base * 0.8, np.where((h >= 18) & (h < 23), base * 1.2, base))
