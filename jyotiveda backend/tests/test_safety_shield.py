"""Property-based tests: for ANY proposed action and ANY grid state, the shield's output is safe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from jyotiveda.safety.shield import GridState, ProposedAction, SafetyLimits, SafetyShield, Verdict

LIM = SafetyLimits(battery_max_charge_kw=100, battery_max_discharge_kw=100, dt_rating_kw=237.5)
NOW = datetime(2026, 7, 15, 19, 0, tzinfo=UTC)
HOMES = {f"H{i}": 0.25 for i in range(40)}


def state(**kw) -> GridState:
    base = dict(
        ts=NOW,
        battery_soc=0.5,
        battery_soh=1.0,
        battery_capacity_kwh=200,
        battery_temp_c=30,
        battery_power_kw=0,
        battery_alarms=(),
        dt_load_kw=150,
        v_min_pu=0.97,
        v_max_pu=1.01,
        telemetry_age_s=5,
        lifeline_kw_by_household=HOMES,
    )
    base.update(kw)
    return GridState(**base)


def action(**kw) -> ProposedAction:
    base = dict(action_id="a", issued_at=NOW, valid_for_s=900, battery_kw=0.0, duration_s=900)
    base.update(kw)
    return ProposedAction(**base)


@settings(max_examples=400, deadline=None)
@given(
    batt=st.floats(-500, 500),
    soc=st.floats(0, 1),
    temp=st.floats(-10, 70),
    soh=st.floats(0.3, 1),
    age=st.floats(0, 600),
    load=st.floats(0, 400),
    vmin=st.floats(0.9, 1.0),
    vmax=st.floats(1.0, 1.08),
    cur=st.floats(-100, 100),
    limits=st.dictionaries(st.sampled_from(sorted(HOMES)), st.floats(0, 3), max_size=40),
    alarm=st.booleans(),
)
def test_shield_output_always_satisfies_invariants(
    batt, soc, temp, soh, age, load, vmin, vmax, cur, limits, alarm
):
    s = state(
        battery_soc=soc,
        battery_temp_c=temp,
        battery_soh=soh,
        telemetry_age_s=age,
        dt_load_kw=load,
        v_min_pu=vmin,
        v_max_pu=vmax,
        battery_power_kw=cur,
        battery_alarms=("BMS_FAULT_CELL_OV",) if alarm else (),
    )
    d = SafetyShield(LIM).validate(action(battery_kw=batt, load_limits_kw=limits), s, now=NOW)
    assert d.action is not None
    a = d.action
    cap = 200 * soh
    h = a.duration_s / 3600
    # S2/S10 fail-safe
    if age > LIM.telemetry_max_age_s or alarm:
        assert a.battery_kw == 0
    # S3 derated power
    assert -LIM.battery_max_charge_kw - 1e-9 <= a.battery_kw <= LIM.battery_max_discharge_kw + 1e-9
    if temp >= LIM.battery_temp_lockout_c:
        assert a.battery_kw == 0
    if temp >= LIM.battery_temp_derate_c or temp <= 0:
        assert a.battery_kw <= max(LIM.battery_max_discharge_kw * 0.5, 0) + 1e-9 or temp <= 0
        assert a.battery_kw >= -1e-9
    # S4 SoC envelope (with efficiency)
    if a.battery_kw > 0:
        assert soc - a.battery_kw * h / LIM.eta_discharge / cap >= LIM.soc_min - 1e-6
    if a.battery_kw < 0:
        assert soc + (-a.battery_kw) * h * LIM.eta_charge / cap <= LIM.soc_max + 1e-6
    # S8 lifeline floors never violated
    assert all(v >= HOMES[k] - 1e-12 for k, v in a.load_limits_kw.items())
    # S9 blast radius
    assert len(a.load_limits_kw) <= LIM.max_curtailed_share * len(HOMES)
    # never enlarges the request
    assert abs(a.battery_kw) <= abs(batt) + 1e-9


def test_expired_command_rejected():
    d = SafetyShield(LIM).validate(
        action(issued_at=NOW - timedelta(hours=2), battery_kw=10), state(), now=NOW
    )
    assert d.verdict == Verdict.REJECTED and d.violations[0]["rule"] == "S1"


def test_safe_command_approved_unchanged():
    d = SafetyShield(LIM).validate(action(battery_kw=40), state(), now=NOW)
    assert d.verdict == Verdict.APPROVED and d.action.battery_kw == 40


def test_charging_that_overloads_dt_is_reduced():
    d = SafetyShield(LIM).validate(action(battery_kw=-80), state(dt_load_kw=220), now=NOW)
    assert any(v["rule"] == "S6" for v in d.violations)
    assert 220 - d.action.battery_kw <= LIM.dt_rating_kw + 1e-6


def test_lifeline_floor_raised():
    d = SafetyShield(LIM).validate(action(load_limits_kw={"H1": 0.05}), state(), now=NOW)
    assert d.action.load_limits_kw["H1"] == 0.25 and d.verdict == Verdict.MODIFIED
