"""Weather providers. Production chains AI weather models (WeatherNext/GraphCast-class, Aurora,
Earth-2) and INSAT-3DR/3DS cloud nowcasts; the open fallback is Open-Meteo's forecast API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class WeatherForecast:
    index: pd.DatetimeIndex
    cloud_transmittance: np.ndarray  # 0..1 multiplier on clear-sky irradiance
    transmittance_sigma: np.ndarray
    temperature_c: np.ndarray
    source: str


class WeatherProvider(Protocol):
    async def forecast(self, lat: float, lon: float, index: pd.DatetimeIndex) -> WeatherForecast: ...


class OpenMeteoProvider:  # pragma: no cover - network
    URL = "https://api.open-meteo.com/v1/forecast"

    async def forecast(self, lat: float, lon: float, index: pd.DatetimeIndex) -> WeatherForecast:
        import httpx

        params = {
            "latitude": lat,
            "longitude": lon,
            "minutely_15": "cloud_cover,temperature_2m,shortwave_radiation",
            "forecast_days": 3,
            "timezone": "Asia/Kolkata",
        }
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(self.URL, params=params)
            r.raise_for_status()
            m = r.json()["minutely_15"]
        df = (
            pd.DataFrame(m)
            .assign(time=lambda d: pd.to_datetime(d["time"]))
            .set_index("time")
            .reindex(index)
            .ffill()
            .bfill()
        )
        cc = df["cloud_cover"].to_numpy() / 100.0
        trans = 1 - 0.75 * cc**3.4  # Kasten–Czeplak cloud attenuation
        return WeatherForecast(
            index, trans, 0.05 + 0.2 * cc * (1 - cc) * 4, df["temperature_2m"].to_numpy(), "open-meteo"
        )


class SyntheticProvider:
    """Deterministic provider for simulation and tests."""

    def __init__(self, transmittance: np.ndarray | None = None, sigma: float = 0.1) -> None:
        self._t, self._s = transmittance, sigma

    async def forecast(self, lat: float, lon: float, index: pd.DatetimeIndex) -> WeatherForecast:
        t = self._t if self._t is not None else np.ones(len(index))
        return WeatherForecast(index, t, np.full(len(index), self._s), np.full(len(index), 31.0), "synthetic")
