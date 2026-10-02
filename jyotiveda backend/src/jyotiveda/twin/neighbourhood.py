"""Synthetic neighbourhood generator (reference site DT-104: 250 kVA, 180 connections, peri-urban Pune)."""

from __future__ import annotations

import numpy as np

from jyotiveda.domain import Battery, ConnectionType, Household, IncomeBand, Neighbourhood, Transformer

REFERENCE_LAT, REFERENCE_LON = 18.5089, 73.9260  # Hadapsar, Pune (illustrative)


def build_neighbourhood(
    transformer_id: str = "DT-104",
    n_connections: int = 180,
    laterals: int = 3,
    buses_per_lateral: int = 10,
    solar_share: float = 0.33,
    battery_kwh: float = 200.0,
    battery_kw: float = 100.0,
    rating_kva: float = 250.0,
    seed: int = 104,
) -> Neighbourhood:
    rng = np.random.default_rng(seed)
    dt = Transformer(
        id=transformer_id,
        name=f"{transformer_id} Sai Nagar",
        feeder_id="FDR-11KV-HADAPSAR-07",
        rating_kva=rating_kva,
        lat=REFERENCE_LAT,
        lon=REFERENCE_LON,
    )
    households: list[Household] = []
    special = {0: ConnectionType.CLINIC, 1: ConnectionType.SCHOOL, 2: ConnectionType.CLINIC}
    for i in range(n_connections):
        lateral = i % laterals
        bus = 1 + lateral * buses_per_lateral + int(rng.integers(0, buses_per_lateral))
        if i in special:
            conn, band = special[i], IncomeBand.COMMERCIAL
        elif i < 3 + 15:
            conn, band = ConnectionType.SHOP, IncomeBand.COMMERCIAL
        else:
            conn = ConnectionType.RESIDENTIAL
            band = IncomeBand.LOW if rng.random() < 0.72 else IncomeBand.MIDDLE
        sanctioned = {
            ConnectionType.CLINIC: 5.0,
            ConnectionType.SCHOOL: 6.0,
            ConnectionType.SHOP: 3.0,
        }.get(conn, 1.5 if band == IncomeBand.LOW else 3.0)
        critical = {ConnectionType.CLINIC: 0.8, ConnectionType.SCHOOL: 0.3}.get(conn, 0.0)
        # ~4% of homes have a medical device (oxygen concentrator / nebuliser) registered as T0
        if conn == ConnectionType.RESIDENTIAL and rng.random() < 0.04:
            critical = 0.3
        livelihood = 0.6 if conn == ConnectionType.SHOP else (0.15 if rng.random() < 0.2 else 0.0)
        has_solar = rng.random() < solar_share and conn != ConnectionType.CLINIC
        households.append(
            Household(
                id=f"{transformer_id}-H{i + 1:03d}",
                transformer_id=transformer_id,
                bus=bus,
                lateral=lateral,
                connection=conn,
                income_band=band,
                sanctioned_kw=sanctioned,
                lifeline_kw=0.25 if conn == ConnectionType.RESIDENTIAL else 0.4,
                critical_kw=critical,
                livelihood_kw=livelihood,
                solar_kwp=float(rng.choice([1.0, 2.0, 3.0])) if has_solar else 0.0,
                has_ev=bool(rng.random() < 0.08),
                lat=REFERENCE_LAT + float(rng.normal(0, 0.0012)),
                lon=REFERENCE_LON + float(rng.normal(0, 0.0012)),
            )
        )
    battery = (
        Battery(
            id=f"{transformer_id}-BESS",
            transformer_id=transformer_id,
            capacity_kwh=battery_kwh,
            max_charge_kw=battery_kw,
            max_discharge_kw=battery_kw,
        )
        if battery_kwh > 0
        else None
    )
    return Neighbourhood(transformer=dt, households=households, battery=battery)
