// Views over the Jyotiveda control loop. Every value rendered here comes from a backend
// response (cycle, risk, dispatch, edge, simulation). Nothing is computed or invented client-side
// beyond formatting.
import type { ReactNode } from "react";
import { CheckCircle2, CircleDashed, XCircle, AlertTriangle } from "lucide-react";
import { Badge, fmt, pct, clock, human } from "./components";
import type {
  Cycle,
  GridRisk,
  ShieldDecision,
  Dispatch,
  Edge,
  OutageResult,
  MarketResult,
  Simulation,
} from "./types";

const ENGINE_LABEL: Record<string, string> = {
  "physics-lindistflow": "Physics risk engine (LinDistFlow)",
  "gnn-graphsage": "Graph neural network (GraphSAGE)",
};
const POLICY_LABEL: Record<string, string> = {
  "mpc-only": "CVaR-MPC (no RL policy loaded)",
  "ppo-residual": "MPC + PPO residual policy",
};

/** Honest model-runtime label: shows the engine the backend actually reports. */
export function ModelRuntime({
  engine,
  policy,
}: {
  engine?: string;
  policy?: string;
}) {
  return (
    <div className="loop-runtime">
      {engine && (
        <span>
          Risk engine <strong>{ENGINE_LABEL[engine] || engine}</strong>
        </span>
      )}
      {policy && (
        <span>
          Dispatch policy <strong>{POLICY_LABEL[policy] || policy}</strong>
        </span>
      )}
      {engine === "physics-lindistflow" && (
        <small>
          GNN implemented in code; trained weights are not loaded, so the physics
          engine is the active runtime.
        </small>
      )}
    </div>
  );
}

export function RiskView({ risk }: { risk: GridRisk }) {
  return (
    <>
      <div className="facts">
        <span>Risk level</span>
        <strong>
          <Badge text={risk.level} />
        </strong>
        <span>Transformer risk</span>
        <strong>{pct(risk.transformer_risk)}</strong>
        <span>Overload risk</span>
        <strong>{pct(risk.overload_risk)}</strong>
        <span>Voltage risk</span>
        <strong>{pct(risk.voltage_risk)}</strong>
        <span>Lowest voltage estimate</span>
        <strong>{fmt(risk.v_min_estimate_pu, 4)} pu</strong>
        <span>Assessed slot</span>
        <strong>{clock(risk.assessed_slot)} IST</strong>
      </div>
      {risk.top_nodes?.length > 0 && (
        <div className="loop-nodes">
          <span>Most stressed nodes</span>
          {risk.top_nodes.slice(0, 3).map((n) => (
            <div key={n.node}>
              <code>{n.node}</code>
              <small>
                {pct(n.risk)} · {fmt(n.v_est_pu, 4)} pu
              </small>
            </div>
          ))}
        </div>
      )}
      <ModelRuntime engine={risk.engine} />
    </>
  );
}

function Stage({
  n,
  name,
  title,
  children,
  tone,
}: {
  n: number;
  name: string;
  title: string;
  children: ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <li className={`loop-stage ${tone || ""}`}>
      <span className="loop-n">{n}</span>
      <div>
        <small>{name}</small>
        <strong>{title}</strong>
        <div className="loop-body">{children}</div>
      </div>
    </li>
  );
}

const verdictTone = (v?: string) =>
  v === "APPROVED" ? "ok" : v === "MODIFIED" ? "warn" : v ? "bad" : undefined;

/** OBSERVE → PREDICT → UNDERSTAND → ALLOCATE → ORCHESTRATE → SHARE & LEARN, from one real cycle. */
export function ControlLoop({ cycle }: { cycle: Cycle }) {
  const b = cycle.reliability_budget;
  const m = cycle.flexibility_market;
  const d = cycle.dispatch;
  return (
    <>
      <ol className="loop">
        <Stage n={1} name="OBSERVE" title="Telemetry">
          Twin slot {clock(cycle.slot)} IST · edge {human(cycle.edge_mode)}
        </Stage>
        <Stage n={2} name="PREDICT" title="Forecast">
          Demand: {cycle.forecast.demand_backend}
          {cycle.forecast.demand_nmae != null &&
            ` (nMAE ${fmt(cycle.forecast.demand_nmae * 100, 2)}%)`}
          <br />
          Solar: {cycle.forecast.solar_backend}
        </Stage>
        <Stage
          n={3}
          name="UNDERSTAND"
          title="Grid risk & reliability gap"
          tone={["HIGH", "CRITICAL"].includes(cycle.risk.level) ? "warn" : "ok"}
        >
          Risk <Badge text={cycle.risk.level} /> · V<sub>min</sub>{" "}
          {fmt(cycle.risk.v_min_estimate_pu, 3)} pu
          <br />
          Gap {fmt(b.forecast_gap_kwh)} kWh · P(shortage){" "}
          {pct(b.shortage_probability)}
        </Stage>
        <Stage
          n={4}
          name="ALLOCATE"
          title="Reliability Budget & flexibility market"
          tone={b.uncovered_kwh > 0 ? "bad" : "ok"}
        >
          {fmt(b.required_kwh)} kWh required · {fmt(b.uncovered_kwh)} kWh
          uncovered
          <br />
          Market {human(m.status)}: {fmt(m.cleared_kwh)} kWh cleared from{" "}
          {m.accepted.length} offers
          {m.clearing_price_inr_per_kwh > 0 &&
            ` at ₹${fmt(m.clearing_price_inr_per_kwh, 2)}/kWh`}
        </Stage>
        <Stage
          n={5}
          name="ORCHESTRATE"
          title="MPC → safety shield → dispatch"
          tone={verdictTone(d.shield?.verdict)}
        >
          MPC {cycle.plan.status} ({cycle.plan.solver},{" "}
          {cycle.plan.scenarios} scenarios, {fmt(cycle.plan.solve_s, 3)} s)
          <br />
          Shield <Badge text={d.shield?.verdict || "UNKNOWN"} /> · dispatch{" "}
          <Badge text={d.state} />
        </Stage>
        <Stage n={6} name="SHARE & LEARN" title="Fairness & audit">
          {m.deferred_for_fairness.length} households deferred for fairness ·
          every transition written to the hash-chained audit ledger
        </Stage>
      </ol>
      <ModelRuntime engine={cycle.risk.engine} policy={cycle.policy.policy} />
      <p className="chart-note">
        Cycle {d.id} computed in {fmt(cycle.cycle_s, 3)} s on the digital twin.
        Values are model outputs, not field measurements.
      </p>
    </>
  );
}

export function ShieldView({ shield }: { shield: ShieldDecision | null }) {
  if (!shield) return <p className="muted">No safety decision recorded.</p>;
  return (
    <div className="shield">
      <div className="shield-head">
        <Badge text={shield.verdict} />
        <span>
          {shield.verdict === "APPROVED"
            ? "Proposed action passed every deterministic check."
            : shield.verdict === "MODIFIED"
              ? "The shield clipped the proposed action to a safe envelope."
              : "The shield blocked the proposed action."}
        </span>
      </div>
      {shield.action && (
        <p className="muted">
          Authorised setpoint: {fmt(shield.action.battery_kw)} kW battery ·
          source {shield.action.source}
        </p>
      )}
      <ul className="shield-checks">
        {shield.violations.map((v, i) => (
          <li key={`${v.rule}-${i}`} className="bad">
            <XCircle size={14} /> {v.rule} {v.message}
          </li>
        ))}
        {shield.checks_passed.map((c) => (
          <li key={c}>
            <CheckCircle2 size={14} /> {c}
          </li>
        ))}
      </ul>
    </div>
  );
}

const LIFECYCLE = [
  "PROPOSED",
  "VALIDATING",
  "AWAITING_APPROVAL",
  "APPROVED",
  "SENT",
  "ACKNOWLEDGED",
  "EXECUTED",
  "VERIFIED",
];
const TERMINAL_BAD = ["FAILED", "REJECTED", "EXPIRED", "CANCELLED"];

/** Dispatch state machine rendered from the backend's own history log. */
export function DispatchLifecycle({ dispatch }: { dispatch: Dispatch }) {
  const hist = dispatch.history || [];
  const reached = new Map(hist.map((h) => [h.to, h]));
  const bad = hist.find((h) => TERMINAL_BAD.includes(h.to));
  const skippedApproval =
    !reached.has("AWAITING_APPROVAL") && reached.has("APPROVED");
  const target = dispatch.command?.battery_kw ?? dispatch.shield?.action?.battery_kw;
  return (
    <div className="lifecycle">
      <ol>
        {LIFECYCLE.filter(
          (s) => !(s === "AWAITING_APPROVAL" && skippedApproval),
        ).map((s) => {
          const h = reached.get(s);
          return (
            <li key={s} className={h ? "done" : ""}>
              {h ? <CheckCircle2 size={15} /> : <CircleDashed size={15} />}
              <div>
                <strong>{human(s)}</strong>
                {h && (
                  <small>
                    {h.at.slice(11, 19)} UTC · {h.actor}
                  </small>
                )}
              </div>
            </li>
          );
        })}
        {bad && (
          <li className="failed">
            <AlertTriangle size={15} />
            <div>
              <strong>{human(bad.to)}</strong>
              <small>{bad.reason}</small>
            </div>
          </li>
        )}
      </ol>
      <div className="facts">
        <span>Command ID</span>
        <strong className="mono">{dispatch.command?.command_id || "Not issued"}</strong>
        <span>Signature</span>
        <strong>
          {dispatch.command
            ? `Ed25519 signed · key ${dispatch.command.kid}`
            : "Not signed yet"}
        </strong>
        <span>Approved by</span>
        <strong>{dispatch.approved_by || "—"}</strong>
        <span>Telemetry verification</span>
        <strong>
          {dispatch.measured_battery_kw == null
            ? "Awaiting telemetry"
            : `${fmt(dispatch.measured_battery_kw)} kW measured vs ${fmt(target)} kW setpoint`}
        </strong>
      </div>
    </div>
  );
}

const EDGE_LADDER = [
  ["CLOUD_COORDINATED", "Cloud plan, live coordination"],
  ["AUTONOMOUS_CACHED_PLAN", "Cloud link lost · follows last signed plan locally"],
  ["AUTONOMOUS_LIFELINE", "Plan expired · protects lifeline loads locally"],
] as const;

export function EdgeResilience({
  edge,
  outage,
}: {
  edge?: Edge;
  outage?: OutageResult | null;
}) {
  const mode = outage?.edge_response.mode || edge?.mode;
  return (
    <>
      <ol className="edge-ladder">
        {EDGE_LADDER.map(([m, d]) => (
          <li key={m} className={mode === m ? "active" : ""}>
            <strong>{human(m)}</strong>
            <small>{d}</small>
          </li>
        ))}
        {mode === "MANUAL_EMERGENCY" && (
          <li className="active">
            <strong>Manual emergency</strong>
            <small>Urja Sakhi lifeline override</small>
          </li>
        )}
      </ol>
      {outage && (
        <div className="notice" role="status">
          <span>
            Cloud link {outage.cloud_link.toLowerCase()}. Edge chose{" "}
            <strong>{human(outage.edge_response.mode)}</strong>
            {outage.edge_response.battery_kw != null &&
              ` · battery ${fmt(outage.edge_response.battery_kw)} kW`}
            {outage.edge_response.verdict &&
              ` · local shield ${outage.edge_response.verdict.toLowerCase()}`}
            .
          </span>
        </div>
      )}
      {edge?.recent_actions?.length ? (
        <div className="loop-nodes">
          <span>Recent local actions</span>
          {edge.recent_actions.slice(-4).reverse().map((a) => (
            <div key={a.action_id}>
              <code>{a.at.slice(11, 19)} UTC</code>
              <small>
                {fmt(a.battery_kw)} kW · {human(a.mode)}
              </small>
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

const KPI_ROWS: [string, string, number, number][] = [
  // key, label, scale, decimals
  ["critical_outage_home_hours", "Critical outage · home-hours", 1, 1],
  ["energy_not_served_kwh", "Energy not served · kWh", 1, 1],
  ["lifeline_availability_in_scarcity", "Lifeline availability · %", 100, 1],
  ["homes_with_uninterrupted_lifeline", "Homes with uninterrupted lifeline", 1, 0],
  ["dt_peak_loading_pct", "Peak transformer loading · %", 1, 1],
  ["v_min_pu", "Minimum voltage · pu", 1, 4],
  ["voltage_violation_slots", "Voltage-violation slots", 1, 0],
  ["battery_final_soc", "Battery final SoC · %", 100, 0],
  ["battery_throughput_kwh", "Battery throughput · kWh", 1, 1],
  ["energy_shifted_kwh", "Flexible energy shifted · kWh", 1, 1],
  ["p2p_traded_kwh", "P2P rooftop solar shared · kWh", 1, 1],
  ["solar_used_kwh", "Solar used locally · kWh", 1, 1],
  ["fairness_gini_served_ratio", "Served-energy Gini (lower is fairer)", 1, 4],
  ["fairness_jain_index", "Jain fairness index", 1, 4],
  ["shield_modified_commands", "Commands modified by safety shield", 1, 0],
];

export function KpiTable({ sim }: { sim: Simulation }) {
  const b = sim.kpis.baseline;
  const j = sim.kpis.jyotiveda;
  return (
    <div className="table-wrap">
      <table className="kpi-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Baseline</th>
            <th>Jyotiveda</th>
          </tr>
        </thead>
        <tbody>
          {KPI_ROWS.filter(([k]) => k in b || k in j).map(([k, label, s, d]) => (
            <tr key={k}>
              <td>{label}</td>
              <td>{fmt(b[k] == null ? null : b[k] * s, d)}</td>
              <td>
                <strong>{fmt(j[k] == null ? null : j[k] * s, d)}</strong>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MarketSummary({ market }: { market: MarketResult }) {
  const assets = market.accepted.reduce<Record<string, number>>((acc, a) => {
    acc[a.asset] = (acc[a.asset] || 0) + a.energy_kwh;
    return acc;
  }, {});
  return (
    <div className="facts">
      <span>Status</span>
      <strong>{human(market.status)}</strong>
      <span>Need / cleared</span>
      <strong>
        {fmt(market.need_kwh)} / {fmt(market.cleared_kwh)} kWh
      </strong>
      <span>Accepted offers</span>
      <strong>{market.accepted.length}</strong>
      <span>Clearing price</span>
      <strong>₹{fmt(market.clearing_price_inr_per_kwh, 2)}/kWh</strong>
      <span>Total incentives</span>
      <strong>₹{fmt(market.total_payment_inr, 0)}</strong>
      <span>Deferred for fairness</span>
      <strong>{market.deferred_for_fairness.length} households</strong>
      {Object.entries(assets)
        .sort((a, b) => b[1] - a[1])
        .map(([a, kwh]) => (
          <span key={a} style={{ display: "contents" }}>
            <span>{human(a)}</span>
            <strong>{fmt(kwh)} kWh</strong>
          </span>
        ))}
    </div>
  );
}
