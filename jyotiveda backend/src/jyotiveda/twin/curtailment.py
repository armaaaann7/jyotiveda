"""Fairness-aware curtailment (water-filling) and baseline rotational shedding."""

from __future__ import annotations

import numpy as np


def water_fill(available: np.ndarray, weights: np.ndarray, need: float) -> np.ndarray:
    """Take `need` kW from `available` (per home) proportionally to weight*available, respecting caps.

    Homes with high Fairness Debt have small weights and are asked last. Returns taken kW per home.
    """
    take = np.zeros_like(available)
    remaining = need
    active = available > 1e-9
    for _ in range(50):
        if remaining <= 1e-9 or not active.any():
            break
        share = np.where(active, weights * available, 0.0)
        tot = share.sum()
        if tot <= 0:
            break
        alloc = remaining * share / tot
        room = available - take
        alloc = np.minimum(alloc, room)
        take += alloc
        remaining -= alloc.sum()
        active = (available - take) > 1e-9
    return take


def rotational_shed(
    demand: np.ndarray, laterals: np.ndarray, deficit: float, slot: int, n_laterals: int
) -> np.ndarray:
    """Baseline DISCOM practice: shed whole laterals (in rotation) until the deficit is covered.

    Returns boolean mask of homes that are dark. Grid-tied rooftop inverters trip (anti-islanding),
    so dark homes get nothing — not even their own solar.
    """
    dark = np.zeros(len(demand), dtype=bool)
    if deficit <= 0:
        return dark
    order = [(slot // 4 + k) % n_laterals for k in range(n_laterals)]  # rotate hourly
    shed = 0.0
    for lat in order:
        if shed >= deficit:
            break
        mask = laterals == lat
        dark |= mask
        shed += demand[mask].sum()
    return dark
