// Command-centre visual layer. Every operational number rendered here is passed in from a backend
// response (or from frontend state derived directly from one). Static strings are explanatory only.
import { useMemo, type ReactNode } from "react";
import {
  Radio,
  LineChart as LineIcon,
  Gauge,
  Scale,
  Cpu,
  BookCheck,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  CircleDashed,
  Loader2,
  ShieldCheck,
  CloudLightning,
  ChevronRight,
} from "lucide-react";
import { fmt, pct, clock, human } from "./components";
import { C, RESOURCE, riskColor } from "./theme";
import type {
  BudgetFull,
  Cycle,
  Dispatch,
  FairRow,
  MarketResult,
  PolicyInfo,
  Simulation,
} from "./types";

/* ------------------------------------------------------------------ small primitives ---- */

export type Tone = "ok" | "warn" | "bad" | "idle" | "info" | "active";

export function Dot({ tone }: { tone: Tone }) {
  return <i className={`dot-s ${tone}`} aria-hidden />;
}

export function Sparkline({
  values,
  color = C.energy,
  width = 96,
  height = 26,
}: {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return null;
  const max = Math.max(...values),
    min = Math.min(...values);
  const span = max - min || 1;
  const pts = values
    .map(
      (v, i) =>
        `${((i / (values.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg width={width} height={height} className="spark" aria-hidden>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.4" />
    </svg>
  );
}

/** Re-keys on value change so the CSS pulse plays once when backend data changes. */
export function Live({ v, className = "" }: { v: string; className?: string }) {
  return (
    <span key={v} className={`live-v ${className}`}>
      {v}
    </span>
  );
}

/* ------------------------------------------------------------------ status strip -------- */

export interface StatusItem {
  k: string;
  v: string;
  tone: Tone;
  title?: string;
}
export function StatusStrip({ items }: { items: StatusItem[] }) {
  return (
    <div className="status-strip" role="list" aria-label="System status">
      {items.map((s) => (
        <div key={s.k} role="listitem" title={s.title || `${s.k}: ${s.v}`}>
          <span>{s.k}</span>
          <strong>
            <Dot tone={s.tone} />
            {s.v}
          </strong>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ telemetry rail ------ */

export interface Tile {
  k: string;
  v: string;
  unit?: string;
  sub?: ReactNode;
  tone?: Tone;
  spark?: number[];
  sparkColor?: string;
  title?: string;
}
export function TelemetryRail({ tiles }: { tiles: Tile[] }) {
  return (
    <div className="telemetry">
      {tiles.map((t) => (
        <div key={t.k} className={`tile ${t.tone || ""}`} title={t.title}>
          <span className="tile-k">{t.k}</span>
          <div className="tile-v">
            <Live v={t.v} />
            {t.unit && <small>{t.unit}</small>}
          </div>
          <div className="tile-sub">
            {t.sub}
            {t.spark && <Sparkline values={t.spark} color={t.sparkColor} />}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ grid flow ------------ */

export interface FlowFrame {
  source: "live" | "replay" | "saved";
  ts: string;
  transformer_id: string;
  solar_kw: number;
  demand_kw: number;
  grid_kw: number;
  grid_cap_kw: number;
  battery_kw: number; // + discharge, − charge (fleet.py: net = demand − solar − pcs.power_kw)
  soc: number | null;
  battery_capacity_kwh: number;
  served_kw: number | null;
  curtailed_kw: number | null;
  shift_kw: number | null;
  homes_total: number;
  homes_ok: number | null;
  homes_dark: number | null;
  baseline_dark: number | null;
  loading_pct: number | null;
  v_min_pu: number | null;
  risk_level?: string;
  gap_probability?: number | null;
  protected_kw?: number | null;
}

const flowStyle = (kw: number, color: string) => {
  const a = Math.abs(kw);
  if (a < 0.5) return { stroke: color, opacity: 0.12, strokeWidth: 2, animation: "none" };
  return {
    stroke: color,
    opacity: 0.95,
    strokeWidth: Math.min(2 + a / 18, 7),
    animationDuration: `${Math.max(0.35, Math.min(3, 45 / a)).toFixed(2)}s`,
  };
};

function Flow({
  d,
  kw,
  color,
  reverse,
  label,
}: {
  d: string;
  kw: number;
  color: string;
  reverse?: boolean;
  label: string;
}) {
  return (
    <g>
      <title>{`${label}: ${fmt(Math.abs(kw))} kW`}</title>
      <path d={d} className="track" />
      <path
        d={d}
        className={`flow ${reverse ? "rev" : ""}`}
        style={flowStyle(kw, color)}
      />
    </g>
  );
}

function Node({
  x,
  y,
  w,
  h,
  label,
  color,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  color: string;
  children: ReactNode;
}) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect width={w} height={h} rx="6" className="node" />
      <rect width="3" height={h} fill={color} opacity=".85" />
      <text x="16" y="22" className="n-label">
        {label}
      </text>
      {children}
    </g>
  );
}

export function GridFlow({ f }: { f: FlowFrame }) {
  const gp = f.gap_probability ?? null;
  const rc = f.risk_level ? riskColor(f.risk_level) : gp == null ? C.muted : gp >= 0.5 ? C.danger : gp > 0 ? C.warn : C.energy;
  const discharging = f.battery_kw > 0.5;
  const charging = f.battery_kw < -0.5;
  const served = f.served_kw ?? f.demand_kw;
  const cells = 60;
  const darkCells =
    f.homes_dark != null && f.homes_total
      ? Math.round((f.homes_dark / f.homes_total) * cells)
      : 0;
  const load = f.loading_pct ?? 0;
  const ring = 2 * Math.PI * 30;
  const stressed = ["HIGH", "CRITICAL"].includes(f.risk_level || "") || (gp ?? 0) >= 0.5;
  return (
    <svg viewBox="0 0 1000 470" className="gridflow" role="img"
      aria-label={`Power flow for ${f.transformer_id}: solar ${fmt(f.solar_kw)} kW, grid ${fmt(f.grid_kw)} kW, battery ${fmt(f.battery_kw)} kW, demand ${fmt(f.demand_kw)} kW`}>
      <defs>
        <pattern id="gf-grid" width="25" height="25" patternUnits="userSpaceOnUse">
          <path d="M25 0H0V25" fill="none" stroke="rgba(140,175,165,.06)" />
        </pattern>
        <radialGradient id="gf-halo">
          <stop offset="0" stopColor={rc} stopOpacity=".28" />
          <stop offset="1" stopColor={rc} stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="1000" height="470" fill="url(#gf-grid)" />

      {/* flows */}
      <Flow d="M250 92 C 320 92 320 200 385 200" kw={f.solar_kw} color={C.solar} label="Solar to transformer" />
      <Flow d="M750 92 C 680 92 680 200 615 200" kw={f.grid_kw} color={C.cyan} label="Upstream grid import" />
      <Flow d="M250 384 C 320 384 320 262 385 262" kw={f.battery_kw} color={C.battery}
        reverse={charging} label={discharging ? "Battery discharging" : charging ? "Battery charging" : "Battery idle"} />
      <Flow d="M615 262 C 650 262 645 372 680 372" kw={served} color={C.energy} label="Supply to homes" />
      <Flow d="M500 300 L 500 352" kw={f.shift_kw ?? 0} color={C.flex} label="Flexible load deferred" />

      {/* transformer */}
      <circle cx="500" cy="231" r={stressed ? 150 : 115} fill="url(#gf-halo)" className={stressed ? "halo-pulse" : ""} />
      <g transform="translate(385 160)">
        <rect width="230" height="140" rx="8" className="node xfmr" style={{ stroke: rc }} />
        <text x="16" y="24" className="n-label">DISTRIBUTION TRANSFORMER</text>
        <text x="16" y="52" className="n-title">{f.transformer_id}</text>
        <text x="16" y="78" className="n-sub">
          {f.risk_level || gp == null ? "RISK " : "GAP P "}
          <tspan fill={rc} fontWeight="600">{f.risk_level || (gp == null ? "—" : pct(gp))}</tspan>
        </text>
        <text x="16" y="100" className="n-sub">
          V<tspan fontSize="9" dy="3">min</tspan>
          <tspan dy="-3"> {f.v_min_pu == null ? "—" : `${fmt(f.v_min_pu, 3)} pu`}</tspan>
        </text>
        <text x="16" y="122" className="n-sub">
          CAP {fmt(f.grid_cap_kw)} kW
        </text>
        <g transform="translate(178 78)">
          <circle r="30" fill="none" stroke="rgba(140,175,165,.15)" strokeWidth="5" />
          <circle r="30" fill="none" stroke={load > 90 ? C.danger : load > 70 ? C.warn : C.energy} strokeWidth="5"
            strokeDasharray={`${(Math.min(load, 100) / 100) * ring} ${ring}`} transform="rotate(-90)" strokeLinecap="round" className="ring" />
          <text textAnchor="middle" y="4" className="n-num">{f.loading_pct == null ? "—" : `${fmt(load, 0)}%`}</text>
          <text textAnchor="middle" y="46" className="n-tiny">LOADING</text>
        </g>
      </g>

      {/* solar */}
      <Node x={30} y={52} w={220} h={80} label="ROOFTOP SOLAR" color={C.solar}>
        <text x="16" y="56" className="n-val"><tspan key={`s${fmt(f.solar_kw)}`} className="pulse">{fmt(f.solar_kw)}</tspan><tspan className="n-unit"> kW</tspan></text>
        <text x="204" y="56" textAnchor="end" className="n-tiny">
          {f.demand_kw > 0 ? `${fmt((f.solar_kw / f.demand_kw) * 100, 0)}% DEMAND` : ""}
        </text>
      </Node>
      {/* upstream */}
      <Node x={750} y={52} w={220} h={80} label="UPSTREAM FEEDER · IMPORT" color={C.cyan}>
        <text x="16" y="56" className="n-val"><tspan key={`g${fmt(f.grid_kw)}`} className="pulse">{fmt(f.grid_kw)}</tspan><tspan className="n-unit"> kW</tspan></text>
        <text x="204" y="56" textAnchor="end" className="n-tiny" fill={f.grid_kw > f.grid_cap_kw + 0.5 ? C.danger : undefined}>
          {f.grid_kw > f.grid_cap_kw + 0.5 ? "OVER " : ""}LIMIT {fmt(f.grid_cap_kw, 0)}
        </text>
      </Node>
      {/* battery */}
      <Node x={30} y={340} w={220} h={96} label="COMMUNITY BATTERY" color={C.battery}>
        {f.battery_capacity_kwh > 0 ? (
          <>
            <text x="16" y="54" className="n-val"><tspan key={`b${fmt(f.battery_kw)}`} className="pulse">{fmt(Math.abs(f.battery_kw))}</tspan><tspan className="n-unit"> kW</tspan></text>
            <text x="204" y="54" textAnchor="end" className="n-sub">SOC {pct(f.soc)}</text>
            <rect x="16" y="66" width="188" height="6" rx="3" fill="rgba(140,175,165,.14)" />
            <rect x="16" y="66" width={188 * (f.soc ?? 0)} height="6" rx="3" fill={C.battery} className="soc-bar" />
            <text x="16" y="86" className="n-tiny" fill={discharging || charging ? C.battery : undefined}>
              {discharging ? "▼ DISCHARGING" : charging ? "▲ CHARGING" : "IDLE"} · {fmt(f.battery_capacity_kwh, 0)} kWh
            </text>
          </>
        ) : (
          <text x="16" y="54" className="n-sub">Not installed</text>
        )}
      </Node>
      {/* flexible loads */}
      <Node x={400} y={352} w={200} h={84} label="FLEXIBLE LOADS" color={C.flex}>
        <text x="16" y="50" className="n-val small">
          {f.shift_kw == null ? "—" : fmt(f.shift_kw)}
          <tspan className="n-unit"> kW deferred</tspan>
        </text>
        <text x="16" y="68" className="n-tiny">
          {f.curtailed_kw == null ? "SHOWN IN SCENARIO REPLAY" : `${fmt(f.curtailed_kw)} kW COMFORT LIMITED`}
        </text>
      </Node>
      {/* homes */}
      <Node x={680} y={300} w={290} h={150} label={`HOMES · ${f.homes_total}`} color={C.energy}>
        <text x="274" y="22" textAnchor="end" className="n-tiny">
          SERVED {fmt(served)} / {fmt(f.demand_kw)} kW
        </text>
        {Array.from({ length: cells }, (_, i) => (
          <rect key={i} x={16 + (i % 15) * 17.5} y={36 + Math.floor(i / 15) * 15} width="12" height="9" rx="1.5"
            fill={i >= cells - darkCells ? C.danger : C.energy} opacity={i >= cells - darkCells ? 0.75 : 0.5} />
        ))}
        <text x="16" y="114" className="n-sub">
          {f.homes_ok == null
            ? `Essential demand ${fmt(f.protected_kw)} kW`
            : `${f.homes_ok} lifeline OK · ${f.homes_dark ?? 0} dark`}
        </text>
        <text x="16" y="134" className="n-tiny">
          {f.baseline_dark != null ? `BASELINE (NO JYOTIVEDA): ${f.baseline_dark} DARK` : "1 CELL ≈ " + fmt(f.homes_total / cells, 0) + " HOMES"}
        </text>
      </Node>
    </svg>
  );
}

/* ------------------------------------------------------------------ shock console ------- */

export type StepState = "pending" | "active" | "done" | "hold" | "fail";
export interface Step {
  k: string;
  label: string;
  state: StepState;
  detail?: ReactNode;
}
const stepIcon = (s: StepState) =>
  s === "done" ? <CheckCircle2 size={14} /> :
  s === "active" ? <Loader2 size={14} className="spin" /> :
  s === "hold" ? <AlertTriangle size={14} /> :
  s === "fail" ? <XCircle size={14} /> : <CircleDashed size={14} />;

export function StepList({ steps }: { steps: Step[] }) {
  let reveal = 0;
  return (
    <ol className="steps">
      {steps.map((s) => (
        <li key={s.k} className={s.state} style={s.state === "done" ? { animationDelay: `${reveal++ * 70}ms` } : undefined}>
          <span className="step-i">{stepIcon(s.state)}</span>
          <div>
            <strong>{s.label}</strong>
            {s.detail && <small>{s.detail}</small>}
          </div>
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------------ pipeline ------------ */

export type StageState = "pending" | "active" | "complete" | "hold";
const STAGES = [
  { k: "observe", name: "OBSERVE", icon: Radio, desc: "Smart-meter, PV, battery and transformer telemetry" },
  { k: "predict", name: "PREDICT", icon: LineIcon, desc: "Probabilistic P10–P90 demand and solar forecasts" },
  { k: "understand", name: "UNDERSTAND", icon: Gauge, desc: "Grid-risk physics and reliability-gap estimation" },
  { k: "allocate", name: "ALLOCATE", icon: Scale, desc: "Reliability Budget and fairness-aware flex market" },
  { k: "orchestrate", name: "ORCHESTRATE", icon: Cpu, desc: "CVaR-MPC → safety shield → signed dispatch" },
  { k: "share", name: "SHARE & LEARN", icon: BookCheck, desc: "Fairness debt update and hash-chained audit" },
] as const;

export function Pipeline({
  cycle,
  running,
  audit,
}: {
  cycle: Cycle | null;
  running: boolean;
  audit: { state: "pending" | "ok" | "bad" | "na"; records?: number };
}) {
  const b = cycle?.reliability_budget;
  const m = cycle?.flexibility_market;
  const d = cycle?.dispatch;
  const holding = d?.state === "AWAITING_APPROVAL";
  const result: Record<string, { lines: ReactNode[]; latency?: string; tone?: Tone }> = cycle
    ? {
        observe: {
          lines: [`Twin slot ${clock(cycle.slot)} IST`, `Edge ${human(cycle.edge_mode)}`],
        },
        predict: {
          lines: [
            cycle.forecast.demand_backend,
            cycle.forecast.demand_nmae != null
              ? `Demand nMAE ${fmt(cycle.forecast.demand_nmae * 100, 2)}%`
              : cycle.forecast.solar_backend,
            cycle.forecast.demand_coverage_80 != null
              ? `P10–P90 coverage ${pct(cycle.forecast.demand_coverage_80)}`
              : "",
          ],
        },
        understand: {
          tone: ["HIGH", "CRITICAL"].includes(cycle.risk.level) ? "warn" : "ok",
          lines: [
            <span key="r">Risk <b style={{ color: riskColor(cycle.risk.level) }}>{cycle.risk.level}</b> · V<sub>min</sub> {fmt(cycle.risk.v_min_estimate_pu, 3)} pu</span>,
            `Gap ${fmt(b?.forecast_gap_kwh)} kWh · P ${pct(b?.shortage_probability)}`,
          ],
        },
        allocate: {
          tone: (b?.uncovered_kwh || 0) > 0 ? "bad" : "ok",
          lines: [
            `${fmt(b?.required_kwh)} kWh budget · ${fmt(b?.uncovered_kwh)} uncovered`,
            m ? `Market ${fmt(m.cleared_kwh)} kWh · ${m.accepted.length} offers` : "",
          ],
        },
        orchestrate: {
          tone: holding ? "warn" : d?.state === "FAILED" || d?.state === "REJECTED" ? "bad" : "ok",
          latency: `MPC solve ${fmt(cycle.plan.solve_s, 3)} s`,
          lines: [
            `MPC ${cycle.plan.status} · ${cycle.plan.scenarios} scenarios`,
            `Shield ${d?.shield?.verdict ?? "—"} · ${human(d?.state || "—")}`,
          ],
        },
        share: {
          lines: [
            audit.state === "ok"
              ? `Audit chain valid · ${audit.records} records`
              : audit.state === "bad"
                ? "Audit chain INVALID"
                : audit.state === "na"
                  ? "Audit not visible to this role"
                  : "Verifying audit chain…",
            m ? `${m.deferred_for_fairness.length} households deferred for fairness` : "",
          ],
        },
      }
    : {};
  const stateOf = (k: string): StageState => {
    if (running) return "active";
    if (!cycle) return "pending";
    if (k === "orchestrate" && holding) return "hold";
    if (k === "share" && audit.state === "pending") return "active";
    return "complete";
  };
  return (
    <div className="pipeline">
      <ol>
        {STAGES.map((s, i) => {
          const st = stateOf(s.k);
          const r = result[s.k];
          const Icon = s.icon;
          return (
            <li key={`${s.k}-${cycle?.dispatch.id || "none"}`} className={`stage ${st} ${r?.tone || ""}`}
              style={st === "complete" ? { animationDelay: `${i * 90}ms` } : undefined}>
              <div className="stage-head">
                <span className="stage-icon"><Icon size={16} /></span>
                <div>
                  <strong>{s.name}</strong>
                  <em className={`chip ${st}`}>{st === "hold" ? "AWAITING APPROVAL" : st.toUpperCase()}</em>
                </div>
              </div>
              <p className="stage-desc">{s.desc}</p>
              <div className="stage-result">
                {r ? r.lines.filter(Boolean).map((l, j) => <div key={j}>{l}</div>) : <div className="dim">No cycle yet</div>}
              </div>
              {r?.latency && <div className="stage-lat">{r.latency}</div>}
              {i < STAGES.length - 1 && <ChevronRight className="stage-arrow" size={16} />}
            </li>
          );
        })}
      </ol>
      {cycle && <EngineeringChain cycle={cycle} />}
    </div>
  );
}

function EngineeringChain({ cycle }: { cycle: Cycle }) {
  const b = cycle.reliability_budget;
  const m = cycle.flexibility_market;
  const d = cycle.dispatch;
  const chain: [string, string, string][] = [
    ["Telemetry", `${clock(cycle.slot)} IST`, "Live twin state at the cycle slot"],
    ["Forecast", cycle.forecast.demand_backend, `Solar: ${cycle.forecast.solar_backend}`],
    ["Grid risk", `${cycle.risk.level} · ${fmt(cycle.risk.transformer_risk * 100, 0)}%`, `Engine ${cycle.risk.engine}; overload ${pct(cycle.risk.overload_risk)}, voltage ${pct(cycle.risk.voltage_risk)}`],
    ["Reliability gap", `${fmt(b.forecast_gap_kwh)} kWh`, `P(shortage) ${pct(b.shortage_probability)}, window ${clock(b.window_start)}–${clock(b.window_end)}`],
    ["Reliability budget", `${fmt(b.required_kwh)} kWh`, `Uncovered ${fmt(b.uncovered_kwh)} kWh · cost ₹${fmt(b.expected_cost_inr, 0)}`],
    ["Flexibility", `${fmt(m.cleared_kwh)} kWh`, `${m.status}; price ₹${fmt(m.clearing_price_inr_per_kwh, 2)}/kWh; ${m.accepted.length} offers`],
    ["MPC", `${cycle.plan.status} · ${fmt(cycle.plan.solve_s, 3)} s`, `${cycle.plan.solver}, ${cycle.plan.scenarios} scenarios, ${cycle.plan.horizon_slots} slots, ₹${fmt(cycle.plan.objective_inr, 0)}`],
    ["Safety shield", d.shield?.verdict || "—", `${d.shield?.checks_passed.length ?? 0} checks passed, ${d.shield?.violations.length ?? 0} violations`],
    ["Dispatch", human(d.state), `${d.id} · proposed ${fmt(d.proposed.battery_kw)} kW`],
    ["Edge", human(cycle.edge_mode), "Gateway mode when the cycle ran"],
    ["Fairness / audit", `${m.deferred_for_fairness.length} deferred`, "Every transition appended to the audit ledger"],
  ];
  return (
    <details className="eng">
      <summary>Engineering view · 11-step technical chain for {d.id} · cycle {fmt(cycle.cycle_s, 3)} s server-side</summary>
      <div className="chain">
        {chain.map(([k, v, t], i) => (
          <div key={k} className="link" title={t}>
            <span>{String(i + 1).padStart(2, "0")} {k}</span>
            <strong>{v}</strong>
            <small>{t}</small>
          </div>
        ))}
      </div>
      <details className="raw">
        <summary>Raw cycle response (JSON)</summary>
        <pre>{JSON.stringify({ ...cycle, plan: { ...cycle.plan, schedule: `${cycle.plan.schedule.length} slots` } }, null, 2)}</pre>
      </details>
    </details>
  );
}

/* ------------------------------------------------------------------ reliability budget -- */

export function BudgetAllocation({ budget }: { budget?: BudgetFull }) {
  if (!budget)
    return <div className="empty-s">Inject a renewable shock or run a reliability cycle to create a Reliability Budget.</div>;
  const req = Math.max(budget.required_kwh, 1e-6);
  const covered = req - budget.uncovered_kwh;
  return (
    <div className="budget2">
      <div className="b-head">
        <div>
          <span className="lbl">REQUIRED</span>
          <div className="big"><Live v={fmt(budget.required_kwh)} /><small>kWh</small></div>
        </div>
        <div className="b-meta">
          <span>Window <b>{clock(budget.window_start)}–{clock(budget.window_end)}</b></span>
          {budget.shortage_probability != null && <span>P(shortage) <b>{pct(budget.shortage_probability)}</b></span>}
          <span>Covered <b>{pct(covered / req)}</b></span>
          {budget.critical_loads_protected != null && (
            <span>Critical loads <b className={budget.critical_loads_protected ? "ok" : "bad"}>{budget.critical_loads_protected ? "PROTECTED" : "AT RISK"}</b></span>
          )}
        </div>
      </div>
      <div className="b-stack">
        {budget.allocation.map((a) => (
          <i key={a.resource} style={{ width: `${(a.energy_kwh / req) * 100}%`, background: RESOURCE[a.resource]?.color || C.muted }} />
        ))}
        {budget.uncovered_kwh > 0 && <i className="unc" style={{ width: `${(budget.uncovered_kwh / req) * 100}%` }} />}
      </div>
      <div className="b-rows">
        {budget.allocation.map((a, i) => {
          const r = RESOURCE[a.resource];
          return (
            <div key={a.resource} className="b-row" title={`${a.note} · ₹${fmt(a.unit_cost_inr, 2)}/kWh`}>
              <span><i style={{ background: r?.color || C.muted }} />{r?.label || human(a.resource)}</span>
              <div className="bar"><b style={{ width: `${(a.energy_kwh / req) * 100}%`, background: r?.color || C.muted, animationDelay: `${i * 80}ms` }} /></div>
              <strong>{fmt(a.energy_kwh)}<small> kWh</small></strong>
              <em>₹{fmt(a.unit_cost_inr, 1)}</em>
            </div>
          );
        })}
        <div className={`b-row unc ${budget.uncovered_kwh > 0 ? "bad" : ""}`}>
          <span><i />Uncovered gap</span>
          <div className="bar"><b style={{ width: `${(budget.uncovered_kwh / req) * 100}%` }} /></div>
          <strong>{fmt(budget.uncovered_kwh)}<small> kWh</small></strong>
          <em />
        </div>
      </div>
      <div className="b-foot">
        Modelled allocation cost <b>₹{fmt(budget.expected_cost_inr, 0)}</b>
        {budget.lifeline_mode != null && <> · Lifeline mode <b>{budget.lifeline_mode ? "ENGAGED" : "OFF"}</b></>}
        <span>Finite resources allocated in merit order — shedding is the last resort, not the plan.</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ safety boundary ----- */

const RULES: [string, string][] = [
  ["S1", "Command freshness"],
  ["S2", "Telemetry freshness"],
  ["S3", "Battery power limit"],
  ["S4", "SoC envelope"],
  ["S5", "Ramp limit"],
  ["S6", "Transformer rating"],
  ["S7", "Voltage ±6%"],
  ["S8", "Lifeline protection"],
  ["S9", "Blast radius"],
  ["S10", "Protection alarms"],
];

export function SafetyBoundary({ dispatch, policy }: { dispatch?: Dispatch | null; policy?: PolicyInfo }) {
  if (!dispatch?.shield)
    return <div className="empty-s">No proposed action yet. The shield validates every optimiser proposal before anything can reach a device.</div>;
  const sh = dispatch.shield;
  const passed = new Set(sh.checks_passed.map((c) => c.split(" ")[0]));
  const viol = new Map(sh.violations.map((v) => [v.rule, v]));
  return (
    <div className="boundary">
      <div className="bd-col">
        <span className="lbl">AI / OPTIMISATION PROPOSAL</span>
        <div className="bd-val">{fmt(dispatch.proposed.battery_kw)}<small> kW battery</small></div>
        <small>flex {fmt(dispatch.proposed.flex_shift_kw)} kW · {dispatch.proposed.load_limits} household limits</small>
        <small>source <b>{dispatch.proposed.source || "mpc"}</b>{policy ? ` · policy ${policy.policy}` : ""}</small>
        <p className="bd-note">Proposals have no device access.</p>
      </div>
      <div className="bd-shield">
        <div className="bd-shield-head">
          <ShieldCheck size={18} />
          <span className="lbl">DETERMINISTIC SAFETY SHIELD</span>
          <em className={`verdict ${sh.verdict.toLowerCase()}`}>{sh.verdict}</em>
        </div>
        <div className="rules">
          {RULES.map(([id, name], i) => {
            const v = viol.get(id);
            const st = v ? (sh.verdict === "REJECTED" ? "bad" : "warn") : passed.has(id) ? "ok" : "idle";
            return (
              <div key={id} className={`rule ${st}`} style={{ animationDelay: `${i * 55}ms` }}
                title={v ? `${id}: ${v.message}` : st === "ok" ? `${id}: pass` : `${id}: not evaluated`}>
                <span>{st === "ok" ? "✓" : st === "warn" ? "⚠" : st === "bad" ? "✕" : "·"}</span>
                <b>{id}</b>
                <small>{v ? v.message : name}</small>
              </div>
            );
          })}
        </div>
      </div>
      <div className="bd-col">
        <span className="lbl">AUTHORISED DISPATCH</span>
        <div className="bd-val">
          {sh.action ? fmt(sh.action.battery_kw) : "BLOCKED"}
          {sh.action && <small> kW</small>}
        </div>
        <small>{sh.verdict === "MODIFIED" ? "clipped to the safe envelope" : sh.verdict === "APPROVED" ? "unchanged from proposal" : "no command issued"}</small>
        <small>state <b>{human(dispatch.state)}</b></small>
        <p className="bd-note">Only shield-authorised, signed commands reach the edge.</p>
      </div>
      <div className="bd-policy">
        <span className="lbl">APPROVAL POLICY</span>
        {(() => {
          const gate = dispatch.history?.find((h) => h.to === "AWAITING_APPROVAL");
          const auto = dispatch.history?.find((h) => h.from === "VALIDATING" && h.to === "APPROVED");
          return gate ? (
            <span>
              Human-in-the-loop: <b>{gate.reason}</b>
              {dispatch.approved_by ? ` · approved by ${dispatch.approved_by}` : " · awaiting operator"}
            </span>
          ) : auto ? (
            <span>Auto-authorised by policy: <b>{auto.reason}</b></span>
          ) : (
            <span>{human(dispatch.state)}</span>
          );
        })()}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ dispatch timeline --- */

export function DispatchTimeline({ dispatch }: { dispatch?: Dispatch | null }) {
  if (!dispatch) return <div className="empty-s">No dispatch yet.</div>;
  const hist = dispatch.history || [];
  const at = new Map(hist.map((h) => [h.to, h]));
  const bad = hist.find((h) => ["FAILED", "REJECTED", "EXPIRED", "CANCELLED"].includes(h.to));
  const seq: [string, string][] = [
    ["PROPOSED", "Proposed"],
    ["VALIDATING", "Validating"],
    ...(at.has("AWAITING_APPROVAL") ? ([["AWAITING_APPROVAL", "Operator"]] as [string, string][]) : []),
    ["APPROVED", "Authorised"],
    ["SENT", "Sent"],
    ["ACKNOWLEDGED", "Acked"],
    ["EXECUTED", "Executed"],
    ["VERIFIED", "Verified"],
  ];
  const target = dispatch.command?.battery_kw ?? dispatch.shield?.action?.battery_kw;
  return (
    <div className="dtl">
      <ol>
        {seq.map(([s, label]) => {
          const h = at.get(s);
          const isBad = bad && s === "VERIFIED" && !h;
          return (
            <li key={s} className={h ? "done" : isBad ? "bad" : dispatch.state === s ? "now" : ""} title={h ? `${h.actor}: ${h.reason}` : undefined}>
              <i />
              <strong>{isBad ? human(bad!.to) : label}</strong>
              <small>{h ? h.at.slice(11, 19) : isBad ? bad!.at.slice(11, 19) : "—"}</small>
            </li>
          );
        })}
      </ol>
      <div className="dtl-facts">
        <div><span>COMMAND</span><b className="mono">{dispatch.command?.command_id || dispatch.id}</b></div>
        <div><span>SIGNATURE</span><b>{dispatch.command ? `Ed25519 · ${dispatch.command.kid}` : "not issued"}</b></div>
        <div><span>REQUESTED</span><b>{fmt(target)} kW</b></div>
        <div><span>MEASURED</span><b>{dispatch.measured_battery_kw == null ? "awaiting" : `${fmt(dispatch.measured_battery_kw)} kW`}</b></div>
        <div><span>APPROVED BY</span><b>{dispatch.approved_by || (at.has("AWAITING_APPROVAL") ? "pending" : "policy (auto)")}</b></div>
        <div><span>TIMES</span><b>UTC</b></div>
      </div>
      {bad && <div className="dtl-bad">{human(bad.to)}: {bad.reason}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ outcomes ------------ */

const OUT: { k: string; label: string; scale?: number; d?: number; better: "lower" | "higher"; unit: string }[] = [
  { k: "critical_outage_home_hours", label: "Critical outage", better: "lower", unit: "home-h" },
  { k: "energy_not_served_kwh", label: "Energy not served", better: "lower", unit: "kWh" },
  { k: "lifeline_availability_in_scarcity", label: "Lifeline availability", scale: 100, better: "higher", unit: "%" },
  { k: "dt_peak_loading_pct", label: "Peak DT loading", better: "lower", unit: "%" },
  { k: "v_min_pu", label: "Minimum voltage", d: 3, better: "higher", unit: "pu" },
  { k: "energy_shifted_kwh", label: "Flexibility shifted", better: "higher", unit: "kWh" },
  { k: "p2p_traded_kwh", label: "P2P solar shared", better: "higher", unit: "kWh" },
  { k: "battery_throughput_kwh", label: "Battery used", better: "higher", unit: "kWh" },
  { k: "fairness_gini_served_ratio", label: "Served-energy Gini", d: 3, better: "lower", unit: "" },
];

export function OutcomeComparison({ sim, compact }: { sim: Simulation; compact?: boolean }) {
  const b = sim.kpis.baseline,
    j = sim.kpis.jyotiveda;
  const rows = (compact ? OUT.slice(0, 5) : OUT).filter((o) => o.k in b && o.k in j);
  return (
    <div className="outcome">
      <div className="o-legend">
        <span><i className="base" />Baseline</span>
        <span><i className="jv" />Jyotiveda</span>
        <em>SIMULATION RESULT · {sim.id}</em>
      </div>
      {rows.map((o) => {
        const s = o.scale || 1;
        const bv = b[o.k] * s,
          jv = j[o.k] * s;
        const max = Math.max(Math.abs(bv), Math.abs(jv), 1e-9);
        const vmin = o.k === "v_min_pu";
        const w = (v: number) => (vmin ? Math.max(0, (v - 0.9) / 0.15) : Math.abs(v) / max) * 100;
        const better = o.better === "lower" ? jv < bv : jv > bv;
        return (
          <div key={o.k} className="o-row">
            <span className="o-l">{o.label}</span>
            <div className="o-bars">
              <div><b className="base" style={{ width: `${w(bv)}%` }} /><em>{fmt(bv, o.d ?? 1)}</em></div>
              <div><b className="jv" style={{ width: `${w(jv)}%` }} /><em className={better ? "good" : ""}>{fmt(jv, o.d ?? 1)}</em></div>
            </div>
            <small>{o.unit}</small>
          </div>
        );
      })}
      <p className="o-note">
        Same modelled day, same physics. Baseline uses no storage, flexibility or coordination. Scenario results — not a real-world guarantee.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ fairness ------------ */

function Gauge2({ v, label, better, sub }: { v?: number | null; label: string; better: "lower" | "higher"; sub?: string }) {
  const r = 34,
    arc = Math.PI * r;
  const frac = v == null ? 0 : Math.max(0, Math.min(1, v));
  const good = v == null ? C.muted : better === "lower" ? (frac < 0.2 ? C.energy : frac < 0.4 ? C.warn : C.danger) : frac > 0.9 ? C.energy : frac > 0.75 ? C.warn : C.danger;
  return (
    <div className="gauge">
      <svg viewBox="0 0 84 50" width="104">
        <path d="M8 44 A34 34 0 0 1 76 44" fill="none" stroke="rgba(140,175,165,.15)" strokeWidth="6" strokeLinecap="round" />
        <path d="M8 44 A34 34 0 0 1 76 44" fill="none" stroke={good} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${frac * arc} ${arc}`} className="ring" />
      </svg>
      <div className="g-v">{v == null ? "—" : fmt(v, 3)}</div>
      <span>{label}</span>
      {sub && <small>{sub}</small>}
    </div>
  );
}

export function FairnessPanel({
  rows,
  rowsLabel,
  gini,
  jain,
  sim,
  market,
}: {
  rows?: FairRow[];
  rowsLabel: string;
  gini?: number | null;
  jain?: number | null;
  sim?: Simulation | null;
  market?: MarketResult | null;
}) {
  const sorted = useMemo(() => [...(rows || [])].sort((a, b) => b.debt - a.debt), [rows]);
  const maxDebt = sorted[0]?.debt || 0;
  const bins = useMemo(() => {
    const n = 16,
      out = Array(n).fill(0) as number[];
    if (!sorted.length || maxDebt <= 0) return out;
    sorted.forEach((r) => out[Math.min(n - 1, Math.floor((r.debt / maxDebt) * n))]++);
    return out;
  }, [sorted, maxDebt]);
  const levels = ["LOW", "MEDIUM", "HIGH"].map((l) => [l, sorted.filter((r) => r.level === l).length] as const);
  const binMax = Math.max(...bins, 1);
  const contributors = useMemo(() => {
    const m = new Map<string, number>();
    market?.accepted.forEach((a) => m.set(a.household_id, (m.get(a.household_id) || 0) + a.energy_kwh));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [market]);
  return (
    <div className="fair">
      <div className="fair-gauges">
        <Gauge2 v={gini} label="Debt Gini" better="lower" sub="ledger · lower is fairer" />
        <Gauge2 v={jain} label="Jain index" better="higher" sub="ledger · 1 = equal" />
        {sim && (
          <div className="fair-ba">
            <span className="lbl">SERVED-ENERGY FAIRNESS · SIMULATION</span>
            <div><span>Gini</span><b className="base">{fmt(sim.kpis.baseline.fairness_gini_served_ratio, 4)}</b><ChevronRight size={12} /><b className="jv">{fmt(sim.kpis.jyotiveda.fairness_gini_served_ratio, 4)}</b></div>
            <div><span>Jain</span><b className="base">{fmt(sim.kpis.baseline.fairness_jain_index, 4)}</b><ChevronRight size={12} /><b className="jv">{fmt(sim.kpis.jyotiveda.fairness_jain_index, 4)}</b></div>
            <small>Baseline → Jyotiveda</small>
          </div>
        )}
      </div>
      <div className="fair-dist">
        <div className="fd-head">
          <span className="lbl">BURDEN DISTRIBUTION · {rowsLabel}</span>
          <span>{levels.map(([l, n]) => <em key={l} className={`lv ${l.toLowerCase()}`}>{l} {n}</em>)}</span>
        </div>
        {maxDebt > 0 ? (
          <div className="hist" title="Households by fairness debt (left = least burdened)">
            {bins.map((n, i) => <i key={i} style={{ height: `${(n / binMax) * 100}%`, opacity: 0.35 + (i / bins.length) * 0.65 }} />)}
          </div>
        ) : (
          <div className="empty-s">Ledger shows no accumulated flexibility burden yet.</div>
        )}
      </div>
      <div className="fair-cols">
        <div>
          <span className="lbl">MOST BURDENED HOUSEHOLDS</span>
          {maxDebt <= 0 && <div className="empty-s">No household has carried flexibility burden yet.</div>}
          {maxDebt > 0 && sorted.slice(0, 5).map((r) => (
            <div key={r.household_id} className="hb" title={`${r.dr_events} DR events · ${fmt(r.energy_shifted_kwh)} kWh shifted · ${fmt(r.energy_curtailed_kwh)} kWh curtailed`}>
              <code>{r.household_id.split("-").at(-1)}</code>
              <div className="bar"><b style={{ width: `${maxDebt ? (r.debt / maxDebt) * 100 : 0}%` }} /></div>
              <em>{fmt(r.debt, 3)}</em>
            </div>
          ))}
        </div>
        <div>
          <span className="lbl">TOP FLEX CONTRIBUTORS{market ? ` · ${market.deferred_for_fairness.length} DEFERRED FOR FAIRNESS` : ""}</span>
          {contributors.length ? contributors.map(([h, kwh]) => (
            <div key={h} className="hb">
              <code>{h.split("-").at(-1)}</code>
              <div className="bar"><b className="c" style={{ width: `${(kwh / contributors[0][1]) * 100}%` }} /></div>
              <em>{fmt(kwh, 2)} kWh</em>
            </div>
          )) : <div className="empty-s">No cleared flexibility offers in view.</div>}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ model honesty ------- */

export function ModelTruth({
  riskEngine,
  policy,
  forecast,
  solver,
}: {
  riskEngine?: string;
  policy?: string;
  forecast?: string;
  solver?: string;
}) {
  const rows: [string, string, Tone, string][] = [
    ["FORECAST", forecast || "—", forecast ? "ok" : "idle", "Reported by forecast metadata"],
    ["GRID RISK", riskEngine === "physics-lindistflow" ? "Physics · LinDistFlow" : riskEngine || "—", riskEngine ? "ok" : "idle", "Reported by /reliability/{dt}/risk"],
    ["GNN", riskEngine === "gnn-graphsage" ? "Active" : riskEngine ? "Architecture ready · weights not loaded" : "—", riskEngine === "gnn-graphsage" ? "ok" : "idle", "GraphSAGE+GATv2 implemented; no checkpoint configured"],
    ["OPTIMISATION", solver ? `CVaR-MPC · ${solver}` : "—", solver ? "ok" : "idle", "Reported by /version or the cycle plan"],
    ["RL / PPO", policy === "ppo-residual" ? "Residual policy active" : policy === "mpc-only" ? "Fallback · not loaded" : "Unknown until a cycle runs", policy === "ppo-residual" ? "ok" : "idle", "Reported by the cycle's policy field"],
  ];
  return (
    <div className="truth">
      {rows.map(([k, v, t, title]) => (
        <div key={k} title={title}>
          <span>{k}</span>
          <strong><Dot tone={t} />{v}</strong>
        </div>
      ))}
    </div>
  );
}

export function ShockIcon() {
  return <CloudLightning size={18} />;
}
