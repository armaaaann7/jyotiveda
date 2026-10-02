"""Unit tests: forecasting, gap, market, budget, fairness, battery, MPC, grid intelligence, audit, signing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from jyotiveda.audit.ledger import AuditLedger
from jyotiveda.battery.model import envelope
from jyotiveda.domain import Battery, BatteryState
from jyotiveda.fairness.debt import FairnessLedger, gini, jain_index
from jyotiveda.flexibility.market import FlexAsset, FlexOffer, clear_market
from jyotiveda.forecasting.backends import GBMQuantileBackend, SeasonalNaiveBackend
from jyotiveda.forecasting.base import QuantileForecast
from jyotiveda.forecasting.service import ForecastService
from jyotiveda.optimization.mpc import MPCInputs, scenario_set, solve_mpc
from jyotiveda.reliability.budget import build_budget
from jyotiveda.security.signing import CommandSigner, CommandVerifier


def daily_series(days=10, noise=0.05, seed=0):
    idx = pd.date_range("2026-07-01", periods=days * 96, freq="15min")
    h = idx.hour + idx.minute / 60
    base = 80 + 60 * np.exp(-((h - 20) ** 2) / 5)
    return pd.Series(
        base * (1 + np.random.default_rng(seed).normal(0, noise, len(idx))), index=idx, name="demand_kw"
    )


# ------------------------------------------------------------------ forecasting --------------
@pytest.mark.parametrize("backend", [SeasonalNaiveBackend(), GBMQuantileBackend()])
def test_forecast_quantiles_monotone_and_accurate(backend):
    s = daily_series()
    fc = backend.forecast(s.iloc[:-96], 96)
    assert np.all(fc.p10 <= fc.p50) and np.all(fc.p50 <= fc.p90)
    actual = s.iloc[-96:].to_numpy()
    assert np.mean(np.abs(actual - fc.p50)) / actual.mean() < 0.1
    assert len(fc.index) == 96 and fc.index[0] == s.index[-96]


def test_gap_forecast_detects_evening_window():
    idx = pd.date_range("2026-07-15", periods=96, freq="15min")
    h = idx.hour.to_numpy()
    demand = QuantileForecast(
        idx, {0.1: np.full(96, 90.0), 0.5: np.full(96, 100.0), 0.9: np.full(96, 110.0)}, "t", "d"
    )
    solar = QuantileForecast(idx, {q: np.zeros(96) for q in (0.1, 0.5, 0.9)}, "t", "s")
    cap = np.where((h >= 18) & (h < 22), 60.0, 250.0)
    g = ForecastService.gap(demand, solar, cap)
    assert len(g.windows) == 1 and g.windows[0]["start"].endswith("18:00:00")
    assert g.expected_shortage_kwh == pytest.approx(40 * 4, rel=0.1)


# ------------------------------------------------------------------ market ------------------
def _offer(hh, e, price, asset=FlexAsset.PUMP, divisible=False):
    now = datetime(2026, 7, 15, 18, tzinfo=UTC)
    return FlexOffer(
        household_id=hh,
        transformer_id="DT-1",
        asset=asset,
        energy_kwh=e,
        max_kw=2,
        window_start=now,
        window_end=now + timedelta(hours=3),
        min_incentive_inr=e * price,
        divisible=divisible,
    )


def test_market_covers_need_at_min_cost():
    offers = [_offer("A", 2, 3), _offer("B", 2, 5), _offer("C", 2, 9)]
    r = clear_market(offers, 3.5)
    assert r.cleared_kwh >= 3.5 and {a["household_id"] for a in r.accepted} == {"A", "B"}
    assert r.clearing_price_inr_per_kwh == pytest.approx(5)  # uniform marginal price


def test_market_defers_high_fairness_debt_household():
    offers = [_offer("TIRED", 2, 3), _offer("FRESH", 2, 4)]
    r = clear_market(offers, 2, debt_by_household={"TIRED": 2.0})
    assert [a["household_id"] for a in r.accepted] == ["FRESH"]
    assert "TIRED" in r.deferred_for_fairness


def test_market_divisible_partial_and_price_cap():
    r = clear_market([_offer("A", 4, 3, divisible=True), _offer("X", 4, 40)], 1.0)
    assert r.accepted[0]["fraction"] == pytest.approx(0.25, abs=1e-6)
    assert any(x["reason"] == "above DR price cap" for x in r.rejected)


# ------------------------------------------------------------------ budget ------------------
def test_budget_merit_order_and_lifeline_mode():
    env = envelope(
        Battery(id="b", transformer_id="d"), BatteryState(battery_id="b", ts=datetime.now(UTC), soc=0.5)
    )
    b = build_budget(
        "DT-1", datetime.now(UTC), datetime.now(UTC), 0.9, 100, 150, 60, env, 400, 10, 20, 5.0, 10, 200
    )
    names = [a.resource for a in b.allocation]
    assert names == [
        "p2p_solar",
        "battery",
        "flexibility_market",
        "auto_load_shift",
        "lifeline_mode_curtailment",
    ]
    assert b.critical_loads_protected and b.lifeline_mode and b.required_kwh == 150
    assert sum(a.energy_kwh for a in b.allocation) == pytest.approx(150)


# ------------------------------------------------------------------ fairness ----------------
def test_fairness_debt_accumulates_and_decays():
    led = FairnessLedger(["a", "b", "c"], np.ones(3))
    for _ in range(10):
        led.update(np.array([1.0, 0, 0]), np.zeros(3), np.ones(3))
    assert led.debt[0] > 0 and led.debt[1] == 0 and led.level(0) == "HIGH"
    d0 = led.debt[0]
    for _ in range(40):
        led.update(np.zeros(3), np.zeros(3), np.ones(3))
    assert led.debt[0] < 0.4 * d0
    assert gini(np.array([1, 1, 1])) == 0 and jain_index(np.array([1, 1, 1])) == 1


# ------------------------------------------------------------------ battery -----------------
def test_battery_envelope_fail_safe_and_derate():
    b = Battery(id="b", transformer_id="d")
    hot = envelope(b, BatteryState(battery_id="b", ts=datetime.now(UTC), soc=0.5, temperature_c=50))
    assert hot.max_charge_kw == 0 and hot.max_discharge_kw == b.max_discharge_kw * 0.5
    stale = envelope(b, BatteryState(battery_id="b", ts=datetime.now(UTC), soc=0.5), telemetry_age_s=999)
    assert stale.max_discharge_kw == 0 and not stale.healthy
    assert (
        0.5
        < envelope(
            b, BatteryState(battery_id="b", ts=datetime.now(UTC), soc=0.5)
        ).degradation_cost_inr_per_kwh
        < 5
    )


# ------------------------------------------------------------------ MPC ---------------------
def test_mpc_protects_lifeline_and_respects_soc():
    T, S = 24, 5
    z, p = scenario_set(S)
    h = np.arange(T)
    prot = np.tile(np.full(T, 40.0), (S, 1))
    other = np.tile(np.full(T, 80.0), (S, 1))
    cap = np.tile(np.where(h >= 16, 50.0, 250.0), (S, 1))
    solar = np.tile(np.where((h > 4) & (h < 12), 60.0, 0.0), (S, 1))
    plan = solve_mpc(
        MPCInputs(
            dt_h=0.25,
            protected_kw=prot,
            livelihood_kw=np.zeros((S, T)),
            other_kw=other,
            flexible_kw=np.full(T, 10.0),
            solar_kw=solar,
            grid_cap_kw=cap,
            tariff_inr=np.full(T, 6.0),
            probs=p,
            dt_rating_kw=237,
            battery_capacity_kwh=100,
            soc0=0.5,
            p_charge_kw=50,
            p_discharge_kw=50,
        )
    )
    assert plan.status.startswith("optimal")
    assert plan.expected_unserved_protected_kw.max() < 1e-4
    assert plan.soc.min() >= 0.1 - 1e-6 and plan.soc.max() <= 0.95 + 1e-6
    assert plan.battery_kw[16:].mean() > 0  # discharges into the scarcity window
    assert plan.shadow_price_energy_inr[16:].mean() > plan.shadow_price_energy_inr[:8].mean()


# ------------------------------------------------------------------ audit + signing ----------
async def test_audit_chain_detects_tampering():
    led = AuditLedger()
    for i in range(5):
        await led.append("u", "ROLE", "x", "dispatch", f"D{i}", "r", {"i": i})
    recs = led.records()
    assert AuditLedger.verify(recs) == (True, None)
    forged = recs[2].__class__(**{**recs[2].to_dict(), "payload": {"i": 999}})
    ok, bad = AuditLedger.verify([*recs[:2], forged, *recs[3:]])
    assert not ok and bad == 3


def test_signed_command_tamper_and_replay():
    s = CommandSigner.from_path_or_ephemeral(None)
    v = CommandVerifier(s.public_pem())
    now = datetime.now(UTC)
    cmd = s.sign(
        {
            "command_id": "c1",
            "transformer_id": "DT",
            "battery_kw": 10,
            "not_before": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
    )
    assert v.verify(cmd)[0]
    assert v.verify(cmd) == (False, "replayed nonce")
    tampered = dict(cmd, battery_kw=100, nonce="other")
    assert v.verify(tampered) == (False, "bad signature")
