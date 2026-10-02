export type Role = "DISCOM_OPERATOR" | "URJA_SAKHI" | "ANALYST";
export interface Battery {
  soc: number;
  power_kw: number;
  capacity_kwh: number;
  temperature_c?: number;
}
export interface Live {
  transformer_id: string;
  ward: string;
  ts: string;
  slot: number;
  demand_kw: number;
  solar_kw: number;
  grid_cap_kw: number;
  net_import_kw: number;
  loading_pct: number;
  protected_kw: number;
  battery: Battery;
  edge_mode: string;
  renewable_share: number;
  shortfall_kw: number;
}
export interface FleetRow extends Live {
  households: number;
  rating_kva: number;
  stress_score: number;
  gap_expected_kwh_24h: number;
  gap_p90_kwh_24h: number;
  battery_usable_kwh: number;
  risk: { level: string; overload_risk: number; voltage_risk: number };
}
export interface Summary {
  transformers: number;
  households: number;
  renewable_share: number;
  at_risk: number;
  fleet_battery_soc: number;
  dispatches: number;
  avg_simulated_critical_outage_reduction_pct: number | null;
  uptime_s: number;
}
export interface Quantile {
  ts: string;
  p10: number;
  p50: number;
  p90: number;
}
export interface Forecast {
  transformer_id: string;
  meta: Record<string, unknown>;
  demand: Quantile[];
  solar: Quantile[];
  gap: {
    expected_shortage_kwh: number;
    p90_shortage_kwh: number;
    windows: Record<string, unknown>[];
    series: {
      ts: string;
      p_shortage: number;
      expected_kw: number;
      p90_kw: number;
    }[];
  };
}
export interface Budget {
  required_kwh: number;
  uncovered_kwh: number;
  expected_cost_inr: number;
  window_start: string;
  window_end: string;
  critical_loads_protected: boolean;
  allocation: {
    resource: string;
    energy_kwh: number;
    unit_cost_inr: number;
    note: string;
  }[];
}
export interface FairRow {
  household_id: string;
  debt: number;
  level: string;
  dr_events: number;
  energy_shifted_kwh: number;
  energy_curtailed_kwh: number;
}
export interface Fairness {
  transformer_id: string;
  gini_debt: number;
  jain_index: number;
  households: FairRow[];
}
export interface Topology {
  nodes: {
    id: string;
    kind: string;
    bus: number;
    lat?: number;
    lon?: number;
  }[];
  edges: { from: string; to: string; kind: string }[];
}
export interface Timeline {
  ts: string;
  demand_kw: number;
  solar_kw: number;
  grid_cap_kw: number;
  gap_probability: number;
  baseline: {
    served_kw: number;
    v_min_pu?: number;
    dt_loading_pct?: number;
    homes_dark: number;
    homes_lifeline_ok: number;
  };
  jyotiveda: {
    served_kw: number;
    homes_dark: number;
    homes_in_lifeline_mode?: number;
    v_min_pu?: number;
    dt_loading_pct?: number;
    homes_lifeline_ok: number;
    battery_kw: number;
    soc: number;
    grid_kw: number;
    curtailed_kw: number;
    shift_out_kw: number;
    shift_in_kw: number;
  };
}
export interface Simulation {
  id: string;
  runtime_s: number;
  scenario: Record<string, unknown>;
  kpis: { baseline: Record<string, number>; jyotiveda: Record<string, number> };
  improvement: Record<string, number | null>;
  timeline: Timeline[];
  events: { ts: string; event: string; message: string }[];
  reliability_budget: BudgetFull;
  flexibility_market?: MarketResult;
  reliability_gap?: Forecast["gap"];
  /** day-ahead CVaR-MPC plan for the simulated day */
  plan?: MpcPlan;
  explanation: {
    decision: string;
    safety: string;
    drivers: { feature: string; contribution: number; level: string }[];
  };
  fairness_debt: FairRow[];
}
export interface Dispatch {
  id: string;
  transformer_id: string;
  state: string;
  reason: string;
  created_at?: string;
  proposed: {
    battery_kw: number;
    flex_shift_kw: number;
    load_limits: number;
    source?: string;
  };
  final?: { battery_kw: number };
  shield: ShieldDecision | null;
  approved_by?: string | null;
  measured_battery_kw?: number | null;
  command?: DispatchCommand | null;
  explanation: Record<string, unknown>;
  history?: DispatchHistory[];
}
/** POST /api/v1/reliability/{dt}/cycle — one pass of the full control loop. */
export interface Cycle {
  transformer_id: string;
  slot: string;
  cycle_s: number;
  forecast: CycleForecastMeta;
  risk: GridRisk;
  reliability_budget: BudgetFull;
  flexibility_market: MarketResult;
  plan: MpcPlan;
  policy: PolicyInfo;
  dispatch: Dispatch;
  edge_mode: string;
}
export interface Edge {
  mode: string;
  cloud_online: boolean;
  outbox_depth: number;
  cached_plan_until: string | null;
  recent_actions: {
    action_id: string;
    battery_kw: number;
    limits: number;
    at: string;
    mode: string;
  }[];
  battery: Record<string, unknown>;
}
export interface Fixture {
  summary: Summary;
  fleet: { count: number; transformers: FleetRow[] };
  forecasts: Record<string, Forecast>;
  topology: Record<string, Topology>;
  fairness: Record<string, Fairness>;
  edge: Record<string, Edge>;
  simulation: Simulation;
  captured_at: string;
}

// ---------------------------------------------------------------------------------------------
// Contracts below were verified against live responses from the FastAPI backend
// (src/jyotiveda/api/routes.py, runtime/platform.py) on 2 Oct 2026.

/** GET /api/v1/reliability/{dt}/risk — `engine` is "physics-lindistflow" unless a GNN checkpoint loads. */
export interface GridRisk {
  engine: string;
  transformer_risk: number;
  overload_risk: number;
  voltage_risk: number;
  critical_node_risk: number;
  v_min_estimate_pu: number;
  level: string;
  top_nodes: { node: string; risk: number; v_est_pu: number }[];
  assessed_slot: string;
}

/** Full reliability-budget object (ReliabilityBudget.to_dict). `Budget` above is the subset the original UI used. */
export interface BudgetFull extends Budget {
  transformer?: string;
  risk_level?: string;
  shortage_probability?: number;
  forecast_gap_kwh?: number;
  critical_load_kwh?: number;
  battery_available_kwh?: number;
  p2p_available_kwh?: number;
  flexibility_offered_kwh?: number;
  auto_shift_available_kwh?: number;
  curtailable_discretionary_kwh?: number;
  lifeline_mode?: boolean;
  flexibility_required_kwh?: number;
}

/** Flexibility market clearing result (ClearingResult.to_dict). */
export interface MarketResult {
  accepted: {
    offer_id: string;
    household_id: string;
    asset: string;
    fraction: number;
    energy_kwh: number;
    max_kw: number;
    window_start: string;
    window_end: string;
    payment_inr: number;
    rebound_kwh: number;
  }[];
  rejected: Record<string, unknown>[];
  need_kwh: number;
  cleared_kwh: number;
  shortfall_kwh: number;
  clearing_price_inr_per_kwh: number;
  total_payment_inr: number;
  status: string;
  fairness_penalty_weight: number;
  deferred_for_fairness: unknown[];
}
/** Kept for the existing api.ts export name. */
export type FlexResult = MarketResult;

export interface MpcPlan {
  status: string;
  solve_s: number;
  objective_inr: number;
  cvar_protected_kwh: number;
  schedule: {
    t: string;
    battery_kw: number;
    soc: number;
    shift_out_kw: number;
    shift_in_kw: number;
    grid_kw: number;
    unserved_protected_kw: number;
    unserved_other_kw: number;
    shadow_price_inr_per_kwh: number;
  }[];
  scenarios: number;
  horizon_slots: number;
  solver: string;
}

/** ResidualPolicy.propose metadata — "mpc-only" when no PPO ONNX policy is loaded. */
export interface PolicyInfo {
  policy: string;
  residual_kw: number;
  healthy: boolean;
}

export interface ShieldDecision {
  verdict: "APPROVED" | "MODIFIED" | "REJECTED" | string;
  /** safety/shield.py flag(): {rule: "S3", message, ...extra numeric context} */
  violations: { rule: string; message: string; [k: string]: unknown }[];
  checks_passed: string[];
  action?: {
    action_id: string;
    battery_kw: number;
    flex_shift_kw: number;
    load_limits_kw: Record<string, number>;
    source: string;
    duration_s: number;
  };
}

export interface DispatchHistory {
  from?: string;
  to: string;
  at: string;
  actor: string;
  reason: string;
}

/** Signed command metadata. Only non-secret fields are rendered. */
export interface DispatchCommand {
  command_id: string;
  transformer_id: string;
  battery_kw: number;
  flex_shift_kw: number;
  not_before: string;
  expires_at: string;
  duration_s: number;
  kid: string;
  [k: string]: unknown;
}

export interface CycleForecastMeta {
  demand_backend: string;
  solar_backend: string;
  demand_nmae?: number;
  demand_coverage_80?: number;
  solar_nmae_daylight?: number;
}

export interface EdgeResponse {
  mode: string;
  battery_kw?: number;
  verdict?: string;
  violations?: { rule: string; message: string; [k: string]: unknown }[];
  [k: string]: unknown;
}

export interface OutageResult {
  transformer_id: string;
  cloud_link: string;
  edge_response: EdgeResponse;
}

export interface AuditRecord {
  seq: number;
  ts: string;
  actor: string;
  actor_role: string;
  action: string;
  entity_type: string;
  entity_id: string;
  reason: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface AuditVerifyResult {
  valid: boolean;
  first_invalid_seq: number | null;
  records: number;
  head: string;
}

/** POST /api/v1/flexibility/offers body (flexibility/market.py FlexOffer). */
export interface FlexOffer {
  id?: string;
  household_id: string;
  transformer_id: string;
  asset: string;
  energy_kwh: number;
  max_kw: number;
  window_start: string;
  window_end: string;
  min_incentive_inr: number;
  divisible?: boolean;
  rebound_ratio?: number;
}

export interface CopilotResponse {
  answer: string;
  mode: "llm" | "offline" | string;
  model?: string;
  tool_calls?: { tool: string; [k: string]: unknown }[];
}

export interface DevTokenResponse {
  access_token: string;
  token_type: string;
  role: Role;
}
