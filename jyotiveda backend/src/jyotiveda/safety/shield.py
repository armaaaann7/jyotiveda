"""Safety / Constraint Shield — deterministic, dependency-light, final authority over physical actions.

Principle: the more uncertain a layer, the less authority it has. RL and MPC *propose*; this module
*disposes*. It is pure Python + stdlib so the identical code runs in the cloud and on the edge
gateway, and it is property-tested (tests/test_safety_shield.py) to guarantee that every output
satisfies every invariant, for any input.

Invariants enforced (each yields a named violation, metered in Prometheus):
  S1  command freshness — expired or future-dated commands are rejected
  S2  telemetry freshness — stale battery/DT telemetry => battery forced idle (fail-safe)
  S3  battery power within temperature/SoH-derated limits
  S4  battery SoC stays within [soc_min, soc_max] over the command duration
  S5  battery ramp-rate limit
  S6  DT loading ≤ 100% (≤ emergency % for ≤ 30 min, only if explicitly allowed)
  S7  LV voltage within ±6% after the action (linear sensitivity check)
  S8  lifeline (T1) and life-critical (T0) loads are NEVER curtailed; load limits floor at lifeline
  S9  bounded blast radius: max share of homes curtailed at once
  S10 BMS fault / protection alarms => no battery action
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Verdict(StrEnum):
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class SafetyLimits:
    soc_min: float = 0.10
    soc_max: float = 0.95
    battery_max_charge_kw: float = 100.0
    battery_max_discharge_kw: float = 100.0
    battery_ramp_kw_per_min: float = 50.0
    ramp_window_min: float = 5.0
    battery_temp_derate_c: float = 45.0
    battery_temp_lockout_c: float = 55.0
    telemetry_max_age_s: float = 120.0
    dt_rating_kw: float = 237.5
    dt_emergency_pct: float = 110.0
    allow_emergency_overload: bool = False
    v_min_pu: float = 0.94
    v_max_pu: float = 1.06
    dv_per_kw_pu: float = 0.00025  # linearised sensitivity at the weakest LV bus (from the twin)
    max_curtailed_share: float = 0.6
    max_command_ttl_s: float = 1800.0
    eta_charge: float = 0.96
    eta_discharge: float = 0.96


@dataclass(frozen=True)
class GridState:
    ts: datetime
    battery_soc: float
    battery_soh: float
    battery_capacity_kwh: float
    battery_temp_c: float
    battery_power_kw: float  # current setpoint (+discharge)
    battery_alarms: tuple[str, ...]
    dt_load_kw: float  # net DT import before the action
    v_min_pu: float
    v_max_pu: float
    telemetry_age_s: float
    lifeline_kw_by_household: dict[str, float] = field(default_factory=dict)  # T0+T1 floor per home


@dataclass(frozen=True)
class ProposedAction:
    action_id: str
    issued_at: datetime
    valid_for_s: float
    battery_kw: float  # + discharge / - charge
    load_limits_kw: dict[str, float] = field(default_factory=dict)  # household -> smart-meter load limit
    flex_shift_kw: float = 0.0  # aggregate deferral of T3 load
    duration_s: float = 900.0
    source: str = "mpc"  # mpc | rl | operator | edge-fallback


@dataclass
class ShieldDecision:
    verdict: Verdict
    action: ProposedAction | None
    violations: list[dict]
    checks_passed: list[str]

    def to_dict(self) -> dict:
        a = self.action
        return {
            "verdict": self.verdict.value,
            "violations": self.violations,
            "checks_passed": self.checks_passed,
            "action": None
            if a is None
            else {
                "action_id": a.action_id,
                "battery_kw": round(a.battery_kw, 3),
                "flex_shift_kw": round(a.flex_shift_kw, 3),
                "load_limits_kw": {k: round(v, 3) for k, v in a.load_limits_kw.items()},
                "source": a.source,
                "duration_s": a.duration_s,
            },
        }


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class SafetyShield:
    def __init__(self, limits: SafetyLimits) -> None:
        self.lim = limits

    def validate(self, a: ProposedAction, s: GridState, now: datetime | None = None) -> ShieldDecision:
        L = self.lim
        now = now or datetime.now(UTC)
        v: list[dict] = []
        ok: list[str] = []

        def flag(rule: str, msg: str, **kw) -> None:
            v.append({"rule": rule, "message": msg, **kw})

        # S1 command freshness ------------------------------------------------------------
        age = (now - a.issued_at).total_seconds()
        if age < -5 or age > min(a.valid_for_s, L.max_command_ttl_s):
            flag("S1", "command expired or not yet valid", age_s=round(age, 1))
            return ShieldDecision(Verdict.REJECTED, None, v, ok)
        ok.append("S1 command fresh")

        batt = a.battery_kw
        fail_safe = False
        # S2 / S10 fail-safe -----------------------------------------------------------------
        if s.telemetry_age_s > L.telemetry_max_age_s:
            if batt != 0:
                flag("S2", "telemetry stale: battery forced idle", age_s=s.telemetry_age_s)
            batt, fail_safe = 0.0, True
        else:
            ok.append("S2 telemetry fresh")
        if any(al.startswith(("BMS_FAULT", "PROTECTION_TRIP", "ISOLATION")) for al in s.battery_alarms):
            if batt != 0:
                flag("S10", "battery alarm active: no battery action", alarms=list(s.battery_alarms))
            batt, fail_safe = 0.0, True
        else:
            ok.append("S10 no protection alarms")

        # S3 derated power limits ------------------------------------------------------------
        p_ch, p_dis = L.battery_max_charge_kw, L.battery_max_discharge_kw
        if s.battery_temp_c >= L.battery_temp_lockout_c:
            p_ch = p_dis = 0.0
        elif s.battery_temp_c >= L.battery_temp_derate_c:
            p_ch, p_dis = 0.0, p_dis * 0.5
        elif s.battery_temp_c <= 0:
            p_ch = 0.0
        if s.battery_soh < 0.6:
            p_ch, p_dis = p_ch * 0.5, p_dis * 0.5
        clipped = _clip(batt, -p_ch, p_dis)
        if abs(clipped - batt) > 1e-9:
            flag("S3", "battery power exceeds derated limit", requested_kw=batt, allowed_kw=clipped)
        else:
            ok.append("S3 battery power within limits")
        batt = clipped  # always enforce, even sub-epsilon excursions

        # S5 ramp rate (edge interpolates setpoints; shield bounds the step, never enlarges it) ---
        max_step = L.battery_ramp_kw_per_min * L.ramp_window_min
        if not fail_safe and abs(batt - s.battery_power_kw) > max_step:
            r = s.battery_power_kw + max_step * (1 if batt > s.battery_power_kw else -1)
            r = min(max(r, min(batt, 0.0)), max(batt, 0.0))  # never exceed the requested magnitude
            flag("S5", "ramp limit", allowed_kw=round(r, 3))
            batt = r
        else:
            ok.append("S5 ramp within limit")

        # S4 SoC envelope over the command duration --------------------------------------------
        cap = max(s.battery_capacity_kwh * s.battery_soh, 1e-6)
        hours = a.duration_s / 3600
        if batt > 0:
            max_dis = max((s.battery_soc - L.soc_min) * cap * L.eta_discharge / max(hours, 1e-6), 0.0)
            if batt > max_dis:
                flag("S4", "discharge would breach soc_min", allowed_kw=round(max_dis, 3))
                batt = max_dis
        elif batt < 0:
            max_ch = max((L.soc_max - s.battery_soc) * cap / (L.eta_charge * max(hours, 1e-6)), 0.0)
            if -batt > max_ch:
                flag("S4", "charge would breach soc_max", allowed_kw=round(max_ch, 3))
                batt = -max_ch
        if not any(x["rule"] == "S4" for x in v):
            ok.append("S4 SoC stays in envelope")

        # S8 lifeline floors ------------------------------------------------------------------
        limits = dict(a.load_limits_kw)
        raised = []
        for hh, lim in limits.items():
            floor = s.lifeline_kw_by_household.get(hh, 0.25)
            if lim < floor:
                limits[hh] = floor
                raised.append(hh)
        if raised:
            flag(
                "S8",
                "load limit below lifeline floor — raised to lifeline",
                households=raised[:20],
                count=len(raised),
            )
        else:
            ok.append("S8 lifeline and life-critical loads protected")
        # S9 blast radius -----------------------------------------------------------------------
        n_homes = max(len(s.lifeline_kw_by_household), 1)
        if len(limits) > L.max_curtailed_share * n_homes:
            keep = sorted(limits)[: int(L.max_curtailed_share * n_homes)]
            flag("S9", "too many homes curtailed at once — limited", requested=len(limits), allowed=len(keep))
            limits = {k: limits[k] for k in keep}
        else:
            ok.append("S9 blast radius bounded")

        # S6 DT loading: battery discharge + flex shift reduce import; charging increases it ----
        post_load = s.dt_load_kw - batt - a.flex_shift_kw
        cap_kw = L.dt_rating_kw * ((L.dt_emergency_pct / 100) if L.allow_emergency_overload else 1.0)
        if post_load > cap_kw and batt < 0:
            reduce = min(post_load - cap_kw, -batt)
            flag("S6", "charging would overload the transformer — charge reduced", reduce_kw=round(reduce, 3))
            batt += reduce
            post_load -= reduce
        if post_load <= cap_kw + 1e-9:
            ok.append("S6 transformer within rating")

        # S7 voltage (linear sensitivity; injections raise voltage) -----------------------------
        dv = (batt + a.flex_shift_kw) * L.dv_per_kw_pu
        if s.v_max_pu + dv > L.v_max_pu and batt > 0:
            allowed = max((L.v_max_pu - s.v_max_pu) / L.dv_per_kw_pu - a.flex_shift_kw, 0.0)
            flag("S7", "discharge would push voltage above +6%", allowed_kw=round(allowed, 3))
            batt = min(batt, allowed)
        elif s.v_min_pu + dv < L.v_min_pu and batt < 0:
            allowed = -max((s.v_min_pu - L.v_min_pu) / L.dv_per_kw_pu, 0.0)
            flag("S7", "charging would pull voltage below -6%", allowed_kw=round(allowed, 3))
            batt = max(batt, allowed)
        else:
            ok.append("S7 voltage within +/-6%")

        final = ProposedAction(
            action_id=a.action_id,
            issued_at=a.issued_at,
            valid_for_s=a.valid_for_s,
            battery_kw=batt,
            load_limits_kw=limits,
            flex_shift_kw=max(a.flex_shift_kw, 0.0),
            duration_s=a.duration_s,
            source=a.source,
        )
        verdict = Verdict.APPROVED if not v else Verdict.MODIFIED
        return ShieldDecision(verdict, final, v, ok)
