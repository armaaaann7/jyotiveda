import { useState, useEffect, useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Network as NetworkIcon,
  ChartNoAxesCombined,
  FlaskConical,
  ShieldCheck,
  Users,
  Zap,
  Settings2,
  Sparkles,
  Menu,
  ChevronDown,
  Activity,
  BatteryCharging,
  Sun,
  MapPin,
  Play,
  Pause,
  Download,
  RefreshCw,
  Send,
  CloudSun,
  Radio,
  ArrowUpRight,
  Loader2,
  CloudLightning,
  Square,
} from "lucide-react";
import { request, ApiError, getApiBaseUrl, apiServices } from "./api";
import type {
  Role,
  Fixture,
  FleetRow,
  Summary,
  Forecast,
  Topology,
  Fairness,
  Budget,
  Simulation,
  Dispatch,
  Cycle,
  Edge,
  GridRisk,
  OutageResult,
} from "./types";
import {
  ModelRuntime,
  RiskView,
  ShieldView,
  DispatchLifecycle,
  EdgeResilience,
  KpiTable,
  MarketSummary,
} from "./loop";
import {
  StatusStrip,
  TelemetryRail,
  GridFlow,
  StepList,
  Pipeline,
  BudgetAllocation,
  SafetyBoundary,
  DispatchTimeline,
  OutcomeComparison,
  FairnessPanel,
  ModelTruth,
  type StatusItem,
  type Tile,
  type FlowFrame,
  type Step,
  type Tone,
} from "./command";
import { C, riskColor } from "./theme";
import {
  fmt,
  pct,
  clock,
  human,
  Badge,
  Panel,
  Metric,
  Empty,
  State,
  Drawer,
  EnergyChart,
  FanChart,
  BudgetView,
  Network,
} from "./components";
const pages = [
  ["overview", "Overview", LayoutDashboard],
  ["twin", "Neighbourhood twin", NetworkIcon],
  ["forecast", "Forecasts & reliability", ChartNoAxesCombined],
  ["simulation", "Simulation lab", FlaskConical],
  ["dispatch", "Dispatch & safety", ShieldCheck],
  ["community", "Community & fairness", Users],
] as const;
const defaultBase = getApiBaseUrl();
const EDGE_SHORT: Record<string, string> = {
  CLOUD_COORDINATED: "Cloud",
  AUTONOMOUS_CACHED_PLAN: "Cached plan",
  AUTONOMOUS_LIFELINE: "Lifeline",
  MANUAL_EMERGENCY: "Emergency",
};
export default function App() {
  const client = useQueryClient();
  const [page, setPage] = useState("overview");
  const [selected, setSelected] = useState("DT-104");
  const [mode, setMode] = useState<"preview" | "api">("preview");
  const [role, setRole] = useState<Role>("DISCOM_OPERATOR");
  const [base, setBase] = useState(defaultBase);
  const [baseDraft, setBaseDraft] = useState(defaultBase);
  const [token, setToken] = useState("");
  const [drawer, setDrawer] = useState<
    "connect" | "copilot" | "household" | "dispatch" | null
  >(null);
  const [mobile, setMobile] = useState(false);
  const [houseId, setHouseId] = useState("");
  const [dispatch, setDispatch] = useState<Dispatch | null>(null);
  const [wsState, setWsState] = useState("Offline snapshot");
  const [lastEvent, setLastEvent] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [manualToken, setManualToken] = useState("");
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const [outage, setOutage] = useState<OutageResult | null>(null);
  // Renewable-shock run: a real backend scenario followed by a real live reliability cycle.
  const [shock, setShock] = useState<{
    phase: "idle" | "simulating" | "cycling" | "awaiting" | "approving" | "done" | "error";
    dt?: string;
    baseline?: FleetRow;
    sim?: Simulation;
    cycle?: Cycle;
    simMs?: number;
    cycleMs?: number;
    t0?: number;
    error?: string;
  }>({ phase: "idle" });
  const [depth, setDepth] = useState(0.6);
  const [tick, setTick] = useState(0);
  const [flowSrc, setFlowSrc] = useState<"live" | "replay">("live");
  const [flowIdx, setFlowIdx] = useState(0);
  const [flowPlay, setFlowPlay] = useState(false);
  const [budgetSrc, setBudgetSrc] = useState<"scenario" | "cycle">("scenario");
  const [result, setResult] = useState<Simulation | null>(null);
  const [simError, setSimError] = useState<Error | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [replay, setReplay] = useState(72);
  const [playing, setPlaying] = useState(false);
  const [search, setSearch] = useState("");
  const [fairFilter, setFairFilter] = useState("ALL");
  const [note, setNote] = useState("");
  const [chat, setChat] = useState<
    { role: string; content: string; mode?: string }[]
  >([]);
  const [message, setMessage] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [hour, setHour] = useState(19);
  const [scenario, setScenario] = useState({
    solar_reduction: 0.6,
    battery_kwh: 200,
    battery_kw: 100,
    soc0: 0.5,
    n_connections: 180,
    flex_participation: 0.6,
    cap_kw: 70,
    start_hour: 17.5,
    end_hour: 22.5,
  });
  const close = useCallback(() => setDrawer(null), []);
  const fixture = useQuery<Fixture>({
    queryKey: ["fixture"],
    queryFn: async () => {
      const r = await fetch("/preview-data.json");
      if (!r.ok)
        throw new Error(
          "Preview snapshot unavailable. Connect your backend to continue.",
        );
      return r.json();
    },
    staleTime: Infinity,
  });
  const api = useCallback(
    <T,>(path: string, body?: unknown, method?: string) =>
      request<T>(base, token, path, body, method),
    [base, token],
  );
  function useData<T>(
    name: string,
    path: string,
    preview: T | undefined,
    enabled = true,
  ) {
    return useQuery<T>({
      queryKey: [mode, base, role, name, selected],
      queryFn: () =>
        mode === "preview" ? Promise.resolve(preview as T) : api<T>(path),
      enabled: enabled && (mode === "api" ? !!token : preview !== undefined),
      refetchInterval: mode === "api" ? 60000 : false,
    });
  }
  const fleet = useData<{ transformers: FleetRow[] }>(
    "fleet",
    "/api/v1/transformers",
    fixture.data?.fleet,
  );
  const summary = useData<Summary>(
    "summary",
    "/api/v1/analytics/summary",
    fixture.data?.summary,
  );
  const fc = useData<Forecast>(
    "forecast",
    `/api/v1/forecast/${selected}?kind=all`,
    fixture.data?.forecasts[selected],
  );
  const risk = useData<GridRisk>(
    "risk",
    `/api/v1/reliability/${selected}/risk`,
    undefined,
    page === "overview" || page === "forecast",
  );
  const topology = useData<Topology>(
    "topology",
    `/api/v1/transformers/${selected}/topology`,
    fixture.data?.topology[selected],
    page === "twin",
  );
  const fair = useData<Fairness>(
    "fairness",
    `/api/v1/fairness/${selected}`,
    fixture.data?.fairness[selected],
    (page === "community" || page === "overview" || drawer === "household") &&
      role !== "ANALYST",
  );
  const edge = useData<Edge>(
    "edge",
    `/api/v1/edge/${selected}`,
    fixture.data?.edge[selected],
    page === "twin" || page === "dispatch" || page === "overview",
  );
  const dispatches = useData<Dispatch[]>(
    "dispatches",
    `/api/v1/dispatch?transformer_id=${selected}`,
    [],
    page === "dispatch" || page === "overview",
  );
  const audit = useData<Record<string, unknown>[]>(
    "audit",
    `/api/v1/audit?limit=15`,
    [],
    page === "dispatch" && role !== "URJA_SAKHI",
  );
  const version = useData<{ mpc_solver: string; forecast_backend: string }>(
    "version",
    "/api/v1/version",
    undefined,
  );
  const auditCheck = useData<{ valid: boolean; records: number }>(
    "auditVerify",
    "/api/v1/audit/verify",
    undefined,
    role !== "URJA_SAKHI",
  );
  const household = useQuery<Record<string, unknown>>({
    queryKey: [mode, base, role, selected, houseId],
    queryFn: () => api(`/api/v1/fairness/${selected}/households/${houseId}`),
    enabled:
      mode === "api" &&
      drawer === "household" &&
      !!houseId &&
      role !== "ANALYST",
  });
  const rows = fleet.data?.transformers || [];
  const row = rows.find((r) => r.transformer_id === selected);
  const sum = summary.data;
  const forecastData =
    fc.data?.demand.map((d, i) => ({
      time: clock(d.ts),
      demand: d.p50,
      solar: fc.data?.solar[i]?.p50,
    })) || [];
  const displayedResult =
    mode === "preview" ? fixture.data?.simulation : result;
  const budget =
    cycle?.transformer_id === selected
      ? cycle.reliability_budget
      : displayedResult?.scenario.transformer_id === selected
        ? displayedResult.reliability_budget
        : undefined;
  const canRun = role !== "URJA_SAKHI";
  const canDispatch = role === "DISCOM_OPERATOR";
  const canHouse = role !== "ANALYST";
  const changeTransformer = (id: string) => {
    setSelected(id);
    setCycle(null);
    setOutage(null);
    setShock({ phase: "idle" });
    setFlowSrc("live");
    setFlowPlay(false);
    setNotice("");
    setHouseId("");
  };
  const go = (id: string) => {
    setPage(id);
    setMobile(false);
    setNotice("");
  };
  const shockRunning = ["simulating", "cycling", "approving"].includes(shock.phase);
  useEffect(() => {
    if (!shockRunning) return;
    const t = setInterval(() => setTick((v) => v + 1), 100);
    return () => clearInterval(t);
  }, [shockRunning]);
  const replaySim =
    shock.sim ?? (mode === "preview" ? fixture.data?.simulation : result) ?? undefined;
  useEffect(() => {
    if (!flowPlay || !replaySim) return;
    const w = (replaySim.scenario.supply_windows as { end_hour: number }[] | undefined)?.[0];
    const end = Math.min(
      replaySim.timeline.length - 1,
      Math.round(((w?.end_hour ?? 22.5) + 0.75) * 4),
    );
    const t = setInterval(
      () =>
        setFlowIdx((i) => {
          if (i >= end) {
            setFlowPlay(false);
            return i;
          }
          return i + 1;
        }),
      150,
    );
    return () => clearInterval(t);
  }, [flowPlay, replaySim]);
  useEffect(() => {
    if (!dispatch) return;
    const fresh = dispatches.data?.find((d) => d.id === dispatch.id);
    // only move forward: a refetch can arrive older than an approve/reject response
    if (fresh && (fresh.history?.length || 0) > (dispatch.history?.length || 0))
      setDispatch(fresh);
  }, [dispatches.data, dispatch]);
  useEffect(() => {
    if (!playing || !displayedResult) return;
    const t = setInterval(
      () =>
        setReplay((v) => {
          if (v >= displayedResult.timeline.length - 1) {
            setPlaying(false);
            return v;
          }
          return v + 1;
        }),
      450,
    );
    return () => clearInterval(t);
  }, [playing, displayedResult]);
  useEffect(() => {
    setPlaying(false);
    setReplay(Math.min(72, (displayedResult?.timeline.length || 1) - 1));
  }, [displayedResult?.id]);
  useEffect(() => {
    if (mode !== "api" || !token) {
      setWsState("Offline snapshot");
      return;
    }
    let socket: WebSocket | undefined,
      timer: ReturnType<typeof setTimeout>,
      stale: ReturnType<typeof setInterval>;
    let stopped = false,
      attempt = 0,
      last = Date.now(),
      refresh = 0;
    function open() {
      if (stopped) return;
      setWsState("Connecting");
      const url = new URL(base);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.pathname = "/ws/live";
      url.search = new URLSearchParams({
        token,
        topics:
          "transformer.state,dispatch.proposed,dispatch.approved,dispatch.executed,dispatch.verified,dispatch.rejected,alarm.raised,simulation.completed",
        transformer_id: selected,
      }).toString();
      socket = new WebSocket(url);
      socket.onopen = () => {
        attempt = 0;
        last = Date.now();
        setWsState("Connected");
      };
      socket.onmessage = (e) => {
        last = Date.now();
        setWsState("Connected");
        try {
          const ev = JSON.parse(e.data);
          if (ev.type !== "hello") {
            setLastEvent(human(ev.type.replaceAll(".", " ")));
            if (Date.now() - refresh > 10000) {
              refresh = Date.now();
              client.invalidateQueries({
                predicate: (q) =>
                  q.queryKey[0] === "api" &&
                  ["fleet", "summary", "dispatches", "edge", "audit"].includes(
                    String(q.queryKey[3]),
                  ),
              });
            }
          }
        } catch {}
      };
      socket.onerror = () => setWsState("Disconnected");
      socket.onclose = (e) => {
        setWsState(e.code === 4401 ? "Session expired" : "Reconnecting");
        if (!stopped && e.code !== 4401)
          timer = setTimeout(open, Math.min(30000, 1000 * 2 ** attempt++));
      };
    }
    open();
    stale = setInterval(() => {
      if (Date.now() - last > 20000) setWsState("Degraded · updates stale");
    }, 10000);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(stale);
      socket?.close();
    };
  }, [mode, base, token, selected, client]);
  const authError = [fleet.error, summary.error, fc.error].find(
    (e) => e instanceof ApiError && e.status === 401,
  );
  async function connect(dev: boolean) {
    setBusy(true);
    setConnectError("");
    try {
      let t = manualToken.trim();
      if (dev) {
        const res = await request<{ access_token: string }>(
          baseDraft,
          "",
          "/api/v1/auth/dev-token",
          { sub: "demo.operator@discom.in", role, transformers: [] },
        );
        t = res.access_token;
      }
      if (!t)
        throw new Error("Paste a bearer token or use the local demo login.");
      const target = new URL(baseDraft);
      if (!["http:", "https:"].includes(target.protocol))
        throw new Error("Use an HTTP or HTTPS backend URL.");
      await request(baseDraft, t, "/api/v1/transformers");
      setBase(baseDraft);
      client.removeQueries({ predicate: (q) => q.queryKey[0] === "api" });
      setToken(t);
      setMode("api");
      setCycle(null);
      setResult(null);
      setChat([]);
      setDrawer(null);
    } catch (e) {
      setConnectError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function runSimulation() {
    if (mode === "preview") {
      setDrawer("connect");
      return;
    }
    setSimBusy(true);
    setSimError(null);
    try {
      const res = await api<Simulation>("/api/v1/simulation/run", {
        transformer_id: selected,
        day: "2026-07-15",
        ...scenario,
        supply_windows: [
          {
            start_hour: scenario.start_hour,
            end_hour: scenario.end_hour,
            cap_kw: scenario.cap_kw,
          },
        ],
        run_power_flow: true,
        seed: 42,
      });
      setResult(res);
      setReplay(72);
      client.invalidateQueries({
        predicate: (q) => q.queryKey[3] === "summary",
      });
    } catch (e) {
      setSimError(e as Error);
    } finally {
      setSimBusy(false);
    }
  }
  async function runCycle() {
    if (mode === "preview") {
      setNotice("Connect the backend to create an operator dispatch.");
      return;
    }
    if (
      !window.confirm(
        "Run the reliability cycle? Validated actions at or below the backend approval threshold may execute automatically in the twin.",
      )
    )
      return;
    setBusy(true);
    try {
      const c = await api<Cycle>(`/api/v1/reliability/${selected}/cycle`, {});
      setCycle(c);
      setNotice(
        `Cycle completed. Dispatch ${c.dispatch.state.toLowerCase().replaceAll("_", " ")}.`,
      );
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function setClock() {
    if (mode === "preview") {
      setNotice(
        "The preview uses a saved 19:00 snapshot. Connect to change the twin clock.",
      );
      return;
    }
    setBusy(true);
    try {
      await api(`/api/v1/transformers/${selected}/clock`, { hour });
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
      setNotice("Twin clock updated.");
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function emergency(enable: boolean) {
    if (
      !window.confirm(
        `${enable ? "Enable" : "Disable"} emergency mode for ${selected}?`,
      )
    )
      return;
    setBusy(true);
    try {
      await api(`/api/v1/dispatch/emergency/${selected}?enable=${enable}`, {});
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
      setNotice("Emergency mode updated.");
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function startReplay(sim: Simulation) {
    const start = Number(sim.scenario.cloud_start_hour ?? 12);
    setFlowIdx(Math.max(0, Math.round(start * 4) - 4));
    setFlowSrc("replay");
    setFlowPlay(true);
  }
  async function injectShock() {
    if (mode === "preview") {
      setDrawer("connect");
      return;
    }
    if (
      !window.confirm(
        `Inject a ${Math.round(depth * 100)}% renewable shock on ${selected}? This runs the backend scenario, then a live reliability cycle (actions at or below the approval threshold may execute in the twin).`,
      )
    )
      return;
    const dt = selected;
    let t = performance.now();
    setShock({ phase: "simulating", dt, baseline: row, t0: Date.now() });
    setFlowSrc("live");
    setFlowPlay(false);
    try {
      const sim = await api<Simulation>(
        `/api/v1/simulation/scenario/renewable-shock?solar_reduction=${depth}&transformer_id=${dt}`,
        {},
      );
      const simMs = performance.now() - t;
      setShock((x) => ({ ...x, phase: "cycling", sim, simMs }));
      setBudgetSrc("scenario");
      startReplay(sim);
      t = performance.now();
      const c = await api<Cycle>(`/api/v1/reliability/${dt}/cycle`, {});
      setCycle(c);
      setShock((x) => ({
        ...x,
        cycle: c,
        cycleMs: performance.now() - t,
        phase: c.dispatch.state === "AWAITING_APPROVAL" ? "awaiting" : "done",
      }));
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
    } catch (e) {
      setShock((x) => ({ ...x, phase: "error", error: (e as Error).message }));
    }
  }
  async function shockDecision(action: "approve" | "reject") {
    const c = shock.cycle;
    if (!c || !window.confirm(`${human(action)} dispatch ${c.dispatch.id}?`)) return;
    setShock((x) => ({ ...x, phase: "approving" }));
    try {
      const d = await api<Dispatch>(
        `/api/v1/dispatch/${c.dispatch.id}/${action}`,
        { note: `${action}d from command centre` },
      );
      const next = { ...c, dispatch: d };
      setCycle(next);
      setShock((x) => ({ ...x, cycle: next, phase: "done" }));
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
    } catch (e) {
      setShock((x) => ({ ...x, phase: "awaiting", error: (e as Error).message }));
    }
  }
  async function cloudOutage() {
    if (mode === "preview") {
      setNotice("Connect the backend to run the edge-autonomy test.");
      return;
    }
    if (
      !window.confirm(
        `Simulate a 10-minute cloud-link loss for ${selected}'s edge gateway? This changes the twin state.`,
      )
    )
      return;
    setBusy(true);
    try {
      const r = await api<OutageResult>(
        `/api/v1/edge/${selected}/simulate-outage?seconds=600`,
        {},
      );
      setOutage(r);
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function decision(action: "approve" | "reject") {
    if (
      !dispatch ||
      !window.confirm(`${human(action)} dispatch ${dispatch.id}?`)
    )
      return;
    setBusy(true);
    try {
      const d = await api<Dispatch>(
        `/api/v1/dispatch/${dispatch.id}/${action}`,
        { note },
      );
      setDispatch(d);
      client.invalidateQueries({ predicate: (q) => q.queryKey[0] === "api" });
      setNotice(`Dispatch ${d.state.toLowerCase()}`);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function send(text = message) {
    if (!text.trim()) return;
    if (mode === "preview") {
      setNotice(
        "Connect the backend to ask Jyoti. This preview does not generate AI answers.",
      );
      return;
    }
    setMessage("");
    setChatBusy(true);
    const old = chat;
    setChat((c) => [...c, { role: "user", content: text }]);
    try {
      const r = await api<{ answer: string; mode: string; model?: string }>(
        "/api/v1/copilot/chat",
        {
          message: text,
          transformer_id: selected,
          history: old.map(({ role, content }) => ({ role, content })),
        },
      );
      setChat((c) => [
        ...c,
        { role: "assistant", content: r.answer, mode: r.mode },
      ]);
    } catch (e) {
      setChat((c) => [
        ...c,
        { role: "assistant", content: (e as Error).message, mode: "error" },
      ]);
    } finally {
      setChatBusy(false);
    }
  }
  function exportResult() {
    if (!displayedResult) return;
    const u = URL.createObjectURL(
      new Blob([JSON.stringify(displayedResult, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = u;
    a.download = `jyotiveda-${displayedResult.id}.json`;
    a.click();
    URL.revokeObjectURL(u);
  }
  const selectedFair = fair.data?.households.find(
    (h) => h.household_id === houseId,
  );
  const activeTitle = pages.find((p) => p[0] === page)?.[1];
  // ------------------------------------------------------------ command-centre derived state
  // Everything below is read from backend responses already held in query/state; nothing is invented.
  const lastDispatch: Dispatch | undefined =
    (cycle?.transformer_id === selected ? cycle.dispatch : undefined) ??
    dispatches.data?.[dispatches.data.length - 1];
  const wsTone: Tone =
    wsState === "Connected"
      ? "ok"
      : /Reconnecting|Connecting|Degraded/.test(wsState)
        ? "warn"
        : "bad";
  const statusItems: StatusItem[] = [
    {
      k: "BACKEND",
      v: mode === "preview" ? "PREVIEW" : fleet.error ? "ERROR" : fleet.isSuccess ? "CONNECTED" : "CONNECTING",
      tone: mode === "preview" ? "idle" : fleet.error ? "bad" : fleet.isSuccess ? "ok" : "warn",
      title: mode === "api" ? base : "Saved snapshot; no live backend",
    },
    {
      k: "STREAM",
      v: mode === "preview" ? "OFF" : wsState.toUpperCase(),
      tone: mode === "preview" ? "idle" : wsTone,
      title: lastEvent ? `Last event: ${lastEvent}` : "WebSocket /ws/live",
    },
    {
      k: "FORECAST",
      v: fc.data ? "ACTIVE" : fc.error ? "ERROR" : "—",
      tone: fc.data ? "ok" : fc.error ? "bad" : "idle",
      title: String(fc.data?.meta.demand_backend ?? ""),
    },
    {
      k: "GRID RISK",
      v: risk.data ? (risk.data.engine === "physics-lindistflow" ? "PHYSICS" : risk.data.engine.toUpperCase()) : "—",
      tone: risk.data ? "ok" : risk.error ? "bad" : "idle",
      title: risk.data ? `Engine ${risk.data.engine}` : "",
    },
    {
      k: "MPC",
      v: version.data ? version.data.mpc_solver : cycle ? cycle.plan.solver : "—",
      tone: cycle && cycle.plan.status !== "optimal" ? "warn" : version.data || cycle ? "ok" : "idle",
      title: cycle ? `Last plan ${cycle.plan.status} in ${cycle.plan.solve_s}s` : "Solver from /api/v1/version",
    },
    {
      k: "RL",
      v: cycle ? (cycle.policy.policy === "mpc-only" ? "NOT LOADED" : "ACTIVE") : "—",
      tone: cycle?.policy.policy === "ppo-residual" ? "ok" : "idle",
      title: "PPO residual policy; MPC runs alone when no ONNX policy is loaded",
    },
    {
      k: "SAFETY",
      v: lastDispatch?.shield ? lastDispatch.shield.verdict : "IN PATH",
      tone: !lastDispatch?.shield
        ? "info"
        : lastDispatch.shield.verdict === "APPROVED"
          ? "ok"
          : lastDispatch.shield.verdict === "MODIFIED"
            ? "warn"
            : "bad",
      title: lastDispatch ? `Last decision on ${lastDispatch.id}` : "Every dispatch passes the shield by design; no decision yet",
    },
    {
      k: "EDGE",
      v: edge.data ? (EDGE_SHORT[edge.data.mode] || edge.data.mode).toUpperCase() : "—",
      tone: !edge.data ? "idle" : edge.data.mode === "CLOUD_COORDINATED" ? "ok" : "warn",
      title: edge.data ? `${edge.data.mode} · outbox ${edge.data.outbox_depth}` : "",
    },
    {
      k: "AUDIT",
      v: role === "URJA_SAKHI" ? "N/A" : auditCheck.data ? (auditCheck.data.valid ? "VERIFIED" : "BROKEN") : "—",
      tone: auditCheck.data ? (auditCheck.data.valid ? "ok" : "bad") : "idle",
      title: auditCheck.data ? `${auditCheck.data.records} hash-chained records` : "",
    },
  ];
  const levelTone = (l?: string): Tone =>
    l === "CRITICAL" ? "bad" : l === "HIGH" ? "warn" : l ? "ok" : "idle";
  const reliabilityState = !row
    ? { v: "—", tone: "idle" as Tone, sub: "" }
    : row.shortfall_kw > 0
      ? { v: "SHORTFALL", tone: "bad" as Tone, sub: `${fmt(row.shortfall_kw)} kW now` }
      : row.gap_p90_kwh_24h > 0
        ? { v: "GAP AHEAD", tone: "warn" as Tone, sub: `${fmt(row.gap_p90_kwh_24h, 0)} kWh P90 · 24 h` }
        : { v: "NORMAL", tone: "ok" as Tone, sub: "No gap forecast" };
  const tiles: Tile[] = row
    ? [
        { k: "TRANSFORMER", v: row.transformer_id, sub: `${row.ward} · ${fmt(row.rating_kva, 0)} kVA` },
        {
          k: "LOADING",
          v: fmt(row.loading_pct),
          unit: "%",
          tone: row.loading_pct > 90 ? "bad" : row.loading_pct > 70 ? "warn" : undefined,
          sub: `${fmt(row.net_import_kw)} kW import`,
        },
        {
          k: "VOLTAGE · MIN",
          v: risk.data ? fmt(risk.data.v_min_estimate_pu, 3) : "—",
          unit: "pu",
          tone: risk.data && risk.data.v_min_estimate_pu < 0.95 ? "warn" : undefined,
          sub: risk.data ? `est. at ${clock(risk.data.assessed_slot)}` : "connect for estimate",
          title: "LinDistFlow minimum-voltage estimate from the risk endpoint",
        },
        {
          k: "SOLAR",
          v: fmt(row.solar_kw),
          unit: "kW",
          spark: fc.data?.solar.slice(0, 96).map((q) => q.p50),
          sparkColor: C.solar,
          sub: "24 h P50",
        },
        {
          k: "DEMAND",
          v: fmt(row.demand_kw),
          unit: "kW",
          spark: fc.data?.demand.slice(0, 96).map((q) => q.p50),
          sparkColor: C.energy,
          sub: "24 h P50",
        },
        {
          k: "BATTERY SOC",
          v: row.battery.capacity_kwh ? pct(row.battery.soc) : "—",
          sub:
            row.battery.power_kw > 0.5
              ? `▼ ${fmt(row.battery.power_kw)} kW discharging`
              : row.battery.power_kw < -0.5
                ? `▲ ${fmt(-row.battery.power_kw)} kW charging`
                : `idle · ${fmt(row.battery.capacity_kwh, 0)} kWh`,
        },
        {
          k: "GRID RISK",
          v: row.risk.level,
          tone: levelTone(row.risk.level),
          sub: `overload ${pct(row.risk.overload_risk)} · voltage ${pct(row.risk.voltage_risk)}`,
        },
        { k: "RELIABILITY", v: reliabilityState.v, tone: reliabilityState.tone, sub: reliabilityState.sub },
        {
          k: "EDGE",
          v: EDGE_SHORT[edge.data?.mode || row.edge_mode] || human(edge.data?.mode || row.edge_mode),
          tone: (edge.data?.mode || row.edge_mode) === "CLOUD_COORDINATED" ? undefined : "warn",
          sub: edge.data ? (edge.data.cloud_online ? "cloud link up" : "cloud link down") : "gateway",
        },
      ]
    : [];
  const liveFrame: FlowFrame | undefined = row
    ? {
            source: mode === "api" ? "live" : "saved",
            ts: row.ts,
            transformer_id: row.transformer_id,
            solar_kw: row.solar_kw,
            demand_kw: row.demand_kw,
            grid_kw: row.net_import_kw,
            grid_cap_kw: row.grid_cap_kw,
            battery_kw: row.battery.power_kw,
            soc: row.battery.capacity_kwh ? row.battery.soc : null,
            battery_capacity_kwh: row.battery.capacity_kwh,
            served_kw: null,
            curtailed_kw: null,
            shift_kw: null,
            homes_total: row.households,
            homes_ok: null,
            homes_dark: null,
            baseline_dark: null,
            loading_pct: row.loading_pct,
            v_min_pu: risk.data?.v_min_estimate_pu ?? null,
            risk_level: row.risk.level,
            protected_kw: row.protected_kw,
          }
    : undefined;
  const replayFrame = replaySim?.timeline[Math.min(flowIdx, (replaySim?.timeline.length || 1) - 1)];
  const frame: FlowFrame | undefined =
    flowSrc === "replay" && replaySim && replayFrame
      ? {
          source: shock.sim || mode === "api" ? "replay" : "saved",
          ts: replayFrame.ts,
          transformer_id: String(replaySim.scenario.transformer_id),
          solar_kw: replayFrame.solar_kw,
          demand_kw: replayFrame.demand_kw,
          grid_kw: replayFrame.jyotiveda.grid_kw,
          grid_cap_kw: replayFrame.grid_cap_kw,
          battery_kw: replayFrame.jyotiveda.battery_kw,
          soc: replayFrame.jyotiveda.soc,
          battery_capacity_kwh: Number(replaySim.scenario.battery_kwh ?? 0),
          served_kw: replayFrame.jyotiveda.served_kw,
          curtailed_kw: replayFrame.jyotiveda.curtailed_kw,
          shift_kw: replayFrame.jyotiveda.shift_out_kw,
          homes_total: Number(replaySim.scenario.n_connections ?? 0),
          homes_ok: replayFrame.jyotiveda.homes_lifeline_ok,
          homes_dark: replayFrame.jyotiveda.homes_dark,
          baseline_dark: replayFrame.baseline.homes_dark,
          loading_pct: replayFrame.jyotiveda.dt_loading_pct ?? null,
          v_min_pu: replayFrame.jyotiveda.v_min_pu ?? null,
          // Simulation timeline has no per-slot risk level; show its real gap probability instead.
          risk_level: undefined,
          gap_probability: replayFrame.gap_probability,
        }
      : liveFrame;
  const sSim = shock.sim;
  const sCyc = shock.cycle;
  const sD = sCyc?.dispatch;
  void tick;
  const elapsed = shock.t0 ? (Date.now() - shock.t0) / 1000 : 0;
  const ph = shock.phase;
  const cloudEv = sSim?.events.find((e) => e.event === "CLOUD_EVENT");
  const shockSteps: Step[] = [
    {
      k: "normal",
      label: "Normal operation",
      state: ph === "idle" ? "pending" : "done",
      detail: shock.baseline
        ? `${fmt(shock.baseline.solar_kw)} kW solar · ${fmt(shock.baseline.demand_kw)} kW demand · risk ${shock.baseline.risk.level}`
        : undefined,
    },
    {
      k: "solar",
      label: "Solar drop",
      state: ph === "simulating" ? "active" : sSim ? "done" : ph === "error" && !sSim ? "fail" : "pending",
      detail: sSim
        ? `${cloudEv?.message ?? "Cloud event"} · ${clock(cloudEv?.ts || "")}–${String(sSim.scenario.cloud_end_hour)}h`
        : ph === "simulating"
          ? `Backend simulating ${Math.round(depth * 100)}% reduction · ${fmt(elapsed, 1)} s`
          : undefined,
    },
    {
      k: "risk",
      label: "Risk rise",
      state: sSim ? "done" : "pending",
      detail: sSim
        ? `Risk ${sSim.reliability_budget.risk_level} · P(shortage) ${pct(sSim.reliability_budget.shortage_probability)}`
        : undefined,
    },
    {
      k: "gap",
      label: "Reliability gap",
      state: sSim ? "done" : "pending",
      detail: sSim?.reliability_gap
        ? `${fmt(sSim.reliability_gap.expected_shortage_kwh)} kWh expected · ${fmt(sSim.reliability_gap.p90_shortage_kwh)} kWh P90`
        : undefined,
    },
    {
      k: "alloc",
      label: "Resource allocation",
      state: sSim ? "done" : "pending",
      detail: sSim
        ? `${fmt(sSim.reliability_budget.required_kwh)} kWh budget · ${fmt(sSim.reliability_budget.uncovered_kwh)} uncovered${sSim.flexibility_market ? ` · ${sSim.flexibility_market.accepted.length} flex offers` : ""}`
        : undefined,
    },
    {
      k: "opt",
      label: "Optimisation (CVaR-MPC)",
      state: sSim ? "done" : "pending",
      detail: sSim?.plan
        ? `${sSim.plan.status} · ${sSim.plan.solver} · ${sSim.plan.scenarios} scenarios · solve ${fmt(sSim.plan.solve_s, 3)} s`
        : undefined,
    },
    {
      k: "safety",
      label: "Safety check · live twin",
      state:
        ph === "cycling"
          ? "active"
          : sD?.shield
            ? sD.shield.verdict === "REJECTED"
              ? "fail"
              : sD.shield.verdict === "MODIFIED"
                ? "hold"
                : "done"
            : "pending",
      detail: sD?.shield
        ? `${sD.shield.verdict} · ${sD.shield.checks_passed.length}/10 checks passed${sD.shield.violations.length ? ` · ${sD.shield.violations.map((v) => v.rule).join(", ")} adjusted` : ""}`
        : undefined,
    },
    {
      k: "dispatch",
      label: "Dispatch",
      state: !sD ? "pending" : sD.state === "AWAITING_APPROVAL" ? "hold" : ["REJECTED", "FAILED"].includes(sD.state) ? "fail" : "done",
      detail: sD
        ? sD.state === "AWAITING_APPROVAL"
          ? `${fmt(sD.proposed.battery_kw)} kW · ${sD.history?.at(-1)?.reason ?? "awaiting approval"}`
          : `${sD.id} · ${human(sD.state)}`
        : undefined,
    },
    {
      k: "verified",
      label: "Telemetry verified",
      state: ph === "approving" ? "active" : sD?.state === "VERIFIED" ? "done" : sD && ["FAILED", "REJECTED"].includes(sD.state) ? "fail" : "pending",
      detail:
        sD?.measured_battery_kw != null
          ? `${fmt(sD.measured_battery_kw)} kW measured vs ${fmt(sD.command?.battery_kw)} kW setpoint`
          : undefined,
    },
  ];
  const ovBudget =
    budgetSrc === "scenario" && replaySim
      ? replaySim.reliability_budget
      : cycle?.transformer_id === selected
        ? cycle.reliability_budget
        : replaySim?.reliability_budget;
  const ovBudgetLabel =
    budgetSrc === "scenario" && replaySim
      ? `Scenario · ${replaySim.id}`
      : cycle?.transformer_id === selected
        ? `Live cycle · ${cycle.dispatch.id}`
        : replaySim
          ? `Scenario · ${replaySim.id}`
          : "";
  const fairRows = useMemo(() => {
    const live = fair.data?.households;
    if (live?.some((h) => h.debt > 0)) return { rows: live, label: "LIVE LEDGER" };
    if (replaySim?.fairness_debt?.length)
      return {
        rows: replaySim.fairness_debt,
        label: `TOP ${replaySim.fairness_debt.length} BY DEBT · AFTER SIMULATED DAY`,
      };
    return { rows: live, label: "LIVE LEDGER" };
  }, [fair.data, replaySim]);
  return (
    <div className="app">
      <a className="skip-link" href="#workspace">
        Skip to workspace
      </a>
      <aside className={`sidebar ${mobile ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-symbol">
            <img src="/jyotiveda-mark.svg" alt="" width="48" height="48" />
          </div>
          <div>
            <strong>jyotiveda</strong>
            <small>GRID RELIABILITY OS</small>
          </div>
        </div>
        <div className="sidebar-caption">WORKSPACE</div>
        <nav>
          {pages
            .filter(
              (p) =>
                !(
                  role === "URJA_SAKHI" &&
                  ["simulation", "dispatch"].includes(p[0])
                ) && !(role === "ANALYST" && p[0] === "community"),
            )
            .map(([id, label, Icon]) => (
              <button
                key={id}
                onClick={() => go(id)}
                className={page === id ? "active" : ""}
              >
                <Icon size={19} />
                {label}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-card">
            <MapPin size={17} />
            <div>
              <strong>Pune reference fleet</strong>
              <p>Neighbourhood-scale reliability</p>
            </div>
          </div>
          <button
            className="sidebar-connect"
            onClick={() => setDrawer("connect")}
          >
            <Settings2 size={17} /> Connection settings
          </button>
          <div className="operator">
            <span className="avatar">
              {role === "URJA_SAKHI" ? "US" : "DO"}
            </span>
            <div>
              <strong>
                {role === "URJA_SAKHI"
                  ? "Community operator"
                  : role === "ANALYST"
                    ? "Grid analyst"
                    : "DISCOM operator"}
              </strong>
              <small>
                {mode === "preview"
                  ? "Preview workspace"
                  : "Authenticated session"}
              </small>
            </div>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-btn mobile-menu"
              onClick={() => setMobile(!mobile)}
              aria-label="Toggle navigation"
            >
              <Menu />
            </button>
            <span>Command centre</span>
            <span className="crumb-slash">/</span>
            <strong>{activeTitle}</strong>
          </div>
          <div className="header-controls">
            <StatusStrip items={statusItems} />
            <button className="connection" onClick={() => setDrawer("connect")}>
              <span
                className={
                  mode === "api" && wsState === "Connected"
                    ? "dot connected"
                    : "dot"
                }
              />
              {mode === "preview" ? "Preview mode" : wsState}
              <ChevronDown size={14} />
            </button>
          </div>
        </header>
        <main id="workspace">
          <div className="page-heading">
            <div>
              <div className="eyebrow">AI-POWERED DISTRIBUTION RELIABILITY</div>
              <h1>
                {page === "overview" ? "Grid command centre" : activeTitle}
              </h1>
              <p>
                {`${row?.ward || selected} · ${selected} · ${row ? `twin ${clock(row.ts)} IST` : ""} · ${mode === "api" ? "connected digital twin" : "saved snapshot"}`}
              </p>
            </div>
            <div className="heading-actions">
              <label className="sr-only" htmlFor="transformer">
                Select neighbourhood
              </label>
              <select
                id="transformer"
                value={selected}
                onChange={(e) => changeTransformer(e.target.value)}
              >
                {rows.map((r) => (
                  <option key={r.transformer_id} value={r.transformer_id}>
                    {r.transformer_id} · {r.ward}
                  </option>
                ))}
              </select>
              <button
                className="icon-btn refresh"
                aria-label="Refresh data"
                onClick={() => client.invalidateQueries()}
              >
                <RefreshCw size={18} />
              </button>
            </div>
          </div>
          {mode === "preview" && (
            <div className="preview-strip">
              <span>
                <Radio size={15} /> Saved backend snapshot ·{" "}
                {fixture.data?.captured_at?.slice(0, 10) || "Loading"} ·
                Controls that change the model require a connection.
              </span>
              <button onClick={() => setDrawer("connect")}>
                Connect backend
              </button>
            </div>
          )}
          {authError && (
            <div className="error">
              Your session expired. Open Connection settings to sign in again.
            </div>
          )}
          {notice && (
            <div className="notice" role="status">
              {notice}
              <button onClick={() => setNotice("")} aria-label="Dismiss notice">
                ×
              </button>
            </div>
          )}
          <State
            loading={fixture.isLoading || fleet.isLoading}
            error={fleet.error || fixture.error}
            onRetry={() => client.invalidateQueries()}
          />
          {page === "overview" && (
            <div className="cc">
              <TelemetryRail tiles={tiles} />
              <div className="cc-main">
                <Panel
                  className="flow-panel"
                  title="Live grid"
                  sub={
                    frame
                      ? frame.source === "live"
                        ? `Digital twin · ${clock(frame.ts)} IST · flows scale with real kW`
                        : `${frame.source === "saved" ? "Saved scenario" : "Scenario replay"} · ${clock(frame.ts)} IST · counterfactual day`
                      : "Waiting for telemetry"
                  }
                  action={
                    <div className="seg" role="group" aria-label="Flow source">
                      <button
                        className={flowSrc === "live" ? "on" : ""}
                        onClick={() => {
                          setFlowSrc("live");
                          setFlowPlay(false);
                        }}
                      >
                        <span className={`live-dot ${mode === "api" ? "on" : ""}`} />
                        {mode === "api" ? "Live" : "Snapshot"}
                      </button>
                      <button
                        className={flowSrc === "replay" ? "on" : ""}
                        disabled={!replaySim}
                        onClick={() => replaySim && startReplay(replaySim)}
                      >
                        Scenario replay
                      </button>
                    </div>
                  }
                >
                  {frame ? <GridFlow f={frame} /> : <div className="empty-s">Loading telemetry…</div>}
                  {flowSrc === "replay" && replaySim && (
                    <div className="replay-bar">
                      <button
                        className="icon-btn"
                        aria-label={flowPlay ? "Pause flow replay" : "Play flow replay"}
                        onClick={() => setFlowPlay(!flowPlay)}
                      >
                        {flowPlay ? <Pause size={16} /> : <Play size={16} />}
                      </button>
                      <input
                        type="range"
                        aria-label="Flow replay time"
                        min="0"
                        max={replaySim.timeline.length - 1}
                        value={flowIdx}
                        onChange={(e) => {
                          setFlowPlay(false);
                          setFlowIdx(+e.target.value);
                        }}
                      />
                      <strong className="mono">{clock(replaySim.timeline[flowIdx]?.ts || "")} IST</strong>
                      <span className="tag">
                        SIMULATION · {replaySim.id} · gap P {pct(replaySim.timeline[flowIdx]?.gap_probability)}
                      </span>
                    </div>
                  )}
                </Panel>
                <Panel
                  className="shock-panel"
                  title="Renewable intermittency"
                  sub="Real backend scenario → live operator cycle"
                >
                  <div className="shock-ctl">
                    <label>
                      <span>Solar reduction</span>
                      <strong className="mono">{Math.round(depth * 100)}%</strong>
                      <input
                        type="range"
                        min="0.3"
                        max="0.9"
                        step="0.05"
                        value={depth}
                        disabled={shockRunning}
                        onChange={(e) => setDepth(+e.target.value)}
                        aria-label="Solar reduction"
                      />
                    </label>
                    <button
                      className="shock-btn"
                      disabled={shockRunning || !canDispatch}
                      onClick={injectShock}
                      title={!canDispatch ? "Requires the DISCOM operator role" : undefined}
                    >
                      {shockRunning ? <Loader2 size={18} className="spin" /> : <CloudLightning size={18} />}
                      {mode === "preview" ? "Connect to inject renewable shock" : "Inject renewable shock"}
                    </button>
                    {shockRunning && (
                      <small className="mono running">
                        {ph === "simulating" ? "Scenario computing on backend" : ph === "cycling" ? "Live cycle running" : "Executing dispatch"} · {fmt(elapsed, 1)} s
                      </small>
                    )}
                  </div>
                  <StepList steps={shockSteps} />
                  {ph === "awaiting" && sD && (
                    <div className="approve-box">
                      <div>
                        <strong>Human-in-the-loop approval</strong>
                        <p>{sD.history?.at(-1)?.reason}</p>
                      </div>
                      <div className="button-row">
                        <button className="primary" onClick={() => shockDecision("approve")}>
                          Approve dispatch
                        </button>
                        <button className="danger" onClick={() => shockDecision("reject")}>
                          Reject
                        </button>
                      </div>
                    </div>
                  )}
                  {ph === "error" && <div className="error">{shock.error}</div>}
                  {sSim && (
                    <div className="shock-out">
                      <div>
                        <span>CRITICAL OUTAGE</span>
                        <b>
                          <del>{fmt(sSim.kpis.baseline.critical_outage_home_hours)}</del>
                          {fmt(sSim.kpis.jyotiveda.critical_outage_home_hours)}
                        </b>
                        <small>home-hours</small>
                      </div>
                      <div>
                        <span>ENERGY NOT SERVED</span>
                        <b>
                          <del>{fmt(sSim.kpis.baseline.energy_not_served_kwh)}</del>
                          {fmt(sSim.kpis.jyotiveda.energy_not_served_kwh)}
                        </b>
                        <small>kWh</small>
                      </div>
                      <em>Baseline → Jyotiveda · simulation</em>
                    </div>
                  )}
                </Panel>
              </div>
              <Panel
                className="pipe-panel"
                title="Control loop"
                sub={
                  cycle?.transformer_id === selected
                    ? `Latest live cycle ${cycle.dispatch.id} · ${fmt(cycle.cycle_s, 3)} s server-side`
                    : "Observe → predict → understand → allocate → orchestrate → share & learn"
                }
                action={
                  canDispatch && (
                    <button className="secondary sm" disabled={busy || mode === "preview"} onClick={runCycle}>
                      <Zap size={14} /> Run cycle
                    </button>
                  )
                }
              >
                <Pipeline
                  cycle={cycle?.transformer_id === selected ? cycle : null}
                  running={ph === "cycling" || (busy && !cycle)}
                  audit={
                    role === "URJA_SAKHI"
                      ? { state: "na" }
                      : auditCheck.isFetching || !auditCheck.data
                        ? { state: "pending" }
                        : { state: auditCheck.data.valid ? "ok" : "bad", records: auditCheck.data.records }
                  }
                />
              </Panel>
              <div className="cc-row budget-safety">
                <Panel
                  title="Reliability Budget"
                  sub={ovBudgetLabel || "Finite local resources allocated to the gap"}
                  action={
                    replaySim && cycle?.transformer_id === selected ? (
                      <div className="seg" role="group" aria-label="Budget source">
                        <button className={budgetSrc === "scenario" ? "on" : ""} onClick={() => setBudgetSrc("scenario")}>
                          Scenario
                        </button>
                        <button className={budgetSrc === "cycle" ? "on" : ""} onClick={() => setBudgetSrc("cycle")}>
                          Live cycle
                        </button>
                      </div>
                    ) : undefined
                  }
                >
                  <BudgetAllocation budget={ovBudget} />
                </Panel>
                <Panel
                  title="Safety boundary"
                  sub={lastDispatch ? `AI proposes · shield decides · ${lastDispatch.id}` : "AI proposes · shield decides"}
                >
                  <SafetyBoundary
                    dispatch={lastDispatch}
                    policy={cycle?.transformer_id === selected ? cycle.policy : undefined}
                  />
                </Panel>
              </div>
              <div className="cc-row dispatch-truth">
                <Panel title="Dispatch lifecycle" sub="Signed command, edge execution, telemetry verification">
                  <DispatchTimeline dispatch={lastDispatch} />
                </Panel>
                <Panel title="Model runtime" sub="What is actually running — reported by the backend">
                  <ModelTruth
                    riskEngine={risk.data?.engine ?? (cycle?.risk.engine)}
                    policy={cycle?.policy.policy}
                    forecast={String(fc.data?.meta.demand_backend ?? cycle?.forecast.demand_backend ?? "") || undefined}
                    solver={version.data?.mpc_solver ?? cycle?.plan.solver}
                  />
                </Panel>
              </div>
              <div className="cc-row outcome-fair">
                <Panel
                  title="Outcome · baseline vs Jyotiveda"
                  sub={replaySim ? `${String(replaySim.scenario.transformer_id)} · ${String(replaySim.scenario.day)} · solar −${pct(Number(replaySim.scenario.solar_reduction))}` : "Run a scenario to compare"}
                >
                  {replaySim ? <OutcomeComparison sim={replaySim} /> : <div className="empty-s">No scenario result yet.</div>}
                </Panel>
                <Panel title="Fairness" sub="Who carries the flexibility burden">
                  {role === "ANALYST" ? (
                    <div className="empty-s">Household fairness data is not available to the analyst role.</div>
                  ) : (
                    <FairnessPanel
                      rows={fairRows.rows}
                      rowsLabel={fairRows.label}
                      gini={fair.data?.gini_debt}
                      jain={fair.data?.jain_index}
                      sim={replaySim}
                      market={replaySim?.flexibility_market ?? (cycle?.transformer_id === selected ? cycle.flexibility_market : undefined)}
                    />
                  )}
                </Panel>
              </div>
              <div className="cc-row fleet-forecast">
                <Panel
                  title="Where to focus"
                  sub="Fleet ranked by forecast and physical stress"
                  action={<span className="count">{rows.length}</span>}
                >
                  <div className="fleet-list">
                    {rows.map((r) => (
                      <button
                        className={`fleet-item ${r.transformer_id === selected ? "selected" : ""}`}
                        onClick={() => changeTransformer(r.transformer_id)}
                        key={r.transformer_id}
                      >
                        <div className="risk-mark" style={{ background: riskColor(r.risk.level) }} />
                        <div className="fleet-name">
                          <strong>{r.ward}</strong>
                          <small>
                            {r.transformer_id} · {r.households} homes · {fmt(r.loading_pct)}% load
                          </small>
                        </div>
                        <div className="fleet-value">
                          <Badge text={r.risk.level} />
                          <small>{fmt(r.gap_p90_kwh_24h, 0)} kWh P90 gap</small>
                        </div>
                      </button>
                    ))}
                  </div>
                </Panel>
                <Panel
                  title="The day ahead"
                  sub="Median demand and solar forecast · kW"
                  action={
                    <button className="text-btn" onClick={() => go("forecast")}>
                      Forecast detail <ArrowUpRight size={14} />
                    </button>
                  }
                >
                  <div className="chart-legend">
                    <span><i style={{ background: C.energy }} />Demand</span>
                    <span><i style={{ background: C.solar }} />Solar</span>
                  </div>
                  <State error={fc.error} />
                  <EnergyChart
                    data={forecastData}
                    height={220}
                    lines={[
                      { key: "demand", label: "Demand", color: C.energy },
                      { key: "solar", label: "Solar", color: C.solar },
                    ]}
                  />
                </Panel>
              </div>
            </div>
          )}
          {page === "twin" && row && (
            <>
              <div className="metrics-grid">
                <Metric
                  label="Transformer loading"
                  value={fmt(row.loading_pct)}
                  unit="%"
                  note={`${fmt(row.rating_kva)} kVA rating`}
                />
                <Metric
                  label="Essential demand"
                  value={fmt(row.protected_kw)}
                  unit="kW"
                  note="Critical and lifeline loads"
                />
                <Metric
                  label="Battery charge"
                  value={row.battery.capacity_kwh ? pct(row.battery.soc) : "—"}
                  note={`${fmt(row.battery.capacity_kwh)} kWh installed`}
                />
                <Metric
                  label="Edge mode"
                  value={human(row.edge_mode)}
                  note={
                    edge.data?.cloud_online
                      ? "Cloud link available"
                      : "Local gateway status"
                  }
                />
              </div>
              {liveFrame && (
                <Panel
                  className="flow-panel"
                  title="Power flow · digital twin"
                  sub={`${clock(liveFrame.ts)} IST · animated flows scale with the twin's real kW · battery direction from PCS sign`}
                >
                  <GridFlow f={liveFrame} />
                </Panel>
              )}
              <Panel
                title="Neighbourhood topology"
                sub="Schematic electrical connections · select a household for details"
                action={<Badge text={row.risk.level} />}
              >
                <State loading={topology.isLoading} error={topology.error} />
                <Network
                  row={row}
                  topology={topology.data}
                  onHousehold={(id) => {
                    if (canHouse) {
                      setHouseId(id);
                      setDrawer("household");
                    }
                  }}
                />
              </Panel>
              <div className="two-col">
                <Panel
                  title="Move the demo clock"
                  sub="Changes the selected twin, not real time"
                >
                  <div className="inline-controls">
                    <input
                      aria-label="Demo hour"
                      type="range"
                      min="0"
                      max="23.75"
                      step=".25"
                      value={hour}
                      onChange={(e) => setHour(+e.target.value)}
                    />
                    <strong>
                      {String(Math.floor(hour)).padStart(2, "0")}:
                      {String(Math.round((hour % 1) * 60)).padStart(2, "0")} IST
                    </strong>
                    <button
                      className="primary"
                      disabled={!canRun || busy}
                      onClick={setClock}
                    >
                      Set time
                    </button>
                  </div>
                  <p className="muted">
                    Data timestamp: {row.ts}. The model uses its scenario date.
                  </p>
                </Panel>
                <Panel
                  title="Local continuity"
                  sub="Edge gateway modes reported by the digital twin"
                >
                  <EdgeResilience edge={edge.data} outage={outage} />
                  <div className="facts">
                    <span>Mode</span>
                    <strong>{human(edge.data?.mode || row.edge_mode)}</strong>
                    <span>Queued events</span>
                    <strong>{fmt(edge.data?.outbox_depth, 0)}</strong>
                    <span>Cached plan until</span>
                    <strong>
                      {edge.data?.cached_plan_until || "No cached plan"}
                    </strong>
                  </div>
                  {canRun && (
                    <button
                      className="secondary edge-test"
                      disabled={mode === "preview" || busy}
                      onClick={cloudOutage}
                    >
                      Simulate cloud-link loss
                    </button>
                  )}
                  {role === "URJA_SAKHI" && (
                    <button
                      className="danger"
                      disabled={mode === "preview" || busy}
                      onClick={() =>
                        emergency(edge.data?.mode !== "MANUAL_EMERGENCY")
                      }
                    >
                      {edge.data?.mode === "MANUAL_EMERGENCY"
                        ? "Disable"
                        : "Enable"}{" "}
                      emergency mode
                    </button>
                  )}
                </Panel>
              </div>
            </>
          )}
          {page === "forecast" && (
            <>
              <State loading={fc.isLoading} error={fc.error} />
              <div className="three-metrics">
                <Metric
                  label="Expected shortage"
                  value={fmt(fc.data?.gap.expected_shortage_kwh)}
                  unit="kWh"
                  note="Across the forecast day"
                />
                <Metric
                  label="P90 shortage"
                  value={fmt(fc.data?.gap.p90_shortage_kwh)}
                  unit="kWh"
                  note="Risk-adjusted planning estimate"
                />
                <Metric
                  label="Forecast source"
                  value={String(
                    fc.data?.meta.demand_backend ||
                      fc.data?.meta.backend ||
                      "See model details",
                  )}
                  note="Actual backend metadata below"
                />
              </div>
              <div className="lower-grid">
                <Panel
                  title="Demand, with uncertainty"
                  sub="kW · median and P10–P90 forecast range"
                >
                  <FanChart
                    data={(fc.data?.demand || []).map((d) => ({
                      ...d,
                      time: clock(d.ts),
                    }))}
                  />
                  <p className="chart-note">
                    P10 and P90 bound the central 80% forecast interval. P50 is
                    the median prediction.
                  </p>
                </Panel>
                <Panel
                  title="Reliability budget"
                  sub="Local resources allocated to the shortage"
                >
                  <BudgetView budget={budget} />
                </Panel>
              </div>
              <Panel
                title="Shortage windows"
                sub="Forecast periods that need attention"
              >
                {fc.data?.gap.windows.length ? (
                  <div className="window-list">
                    {fc.data.gap.windows.map((w, i) => (
                      <div key={i} className="window">
                        <ClockWindow data={w} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty>No shortage windows returned for this forecast.</Empty>
                )}
                <details>
                  <summary>Model details</summary>
                  <pre>{JSON.stringify(fc.data?.meta, null, 2)}</pre>
                </details>
              </Panel>
              <Panel
                title="Grid risk"
                sub="Physical stress assessed by the backend risk engine"
                action={risk.data && <Badge text={risk.data.level} />}
              >
                <State loading={risk.isLoading} error={risk.error} />
                {risk.data ? (
                  <RiskView risk={risk.data} />
                ) : (
                  !risk.isLoading &&
                  !risk.error && (
                    <Empty>
                      {mode === "preview"
                        ? "Connect the backend to assess live grid risk."
                        : "No risk assessment returned."}
                    </Empty>
                  )
                )}
              </Panel>
              {cycle?.transformer_id === selected && (
                <Panel
                  title="Control loop · latest cycle"
                  sub="Observe → predict → understand → allocate → orchestrate → share & learn"
                >
<Pipeline
                    cycle={cycle}
                    running={false}
                    audit={
                      role === "URJA_SAKHI"
                        ? { state: "na" }
                        : auditCheck.isFetching || !auditCheck.data
                          ? { state: "pending" }
                          : { state: auditCheck.data.valid ? "ok" : "bad", records: auditCheck.data.records }
                    }
                  />
                  <ModelRuntime engine={cycle.risk.engine} policy={cycle.policy.policy} />
                </Panel>
              )}
              {canDispatch && (
                <div className="action-bar">
                  <div>
                    <strong>Turn the forecast into an operating plan</strong>
                    <p>
                      This cycle can dispatch actions automatically below the
                      backend approval threshold.
                    </p>
                  </div>
                  <button
                    className="primary"
                    onClick={runCycle}
                    disabled={busy}
                  >
                    {busy ? (
                      <Loader2 className="spin" size={16} />
                    ) : (
                      <Zap size={16} />
                    )}{" "}
                    Run reliability cycle
                  </button>
                </div>
              )}
            </>
          )}
          {page === "simulation" && (
            <div className="simulation-layout">
              <Panel
                title="Build a scenario"
                sub="Explore one simulated day"
                className="scenario-controls"
              >
                <label>
                  Solar generation reduction{" "}
                  <strong>{pct(scenario.solar_reduction)}</strong>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step=".05"
                    value={scenario.solar_reduction}
                    disabled={mode === "preview"}
                    onChange={(e) =>
                      setScenario({
                        ...scenario,
                        solar_reduction: +e.target.value,
                      })
                    }
                  />
                  <span className="range-labels">
                    <span>Clear sky</span>
                    <span>Full loss</span>
                  </span>
                </label>
                <div className="input-grid">
                  <label>
                    Battery capacity · kWh
                    <input
                      type="number"
                      min="0"
                      max="2000"
                      value={scenario.battery_kwh}
                      disabled={mode === "preview"}
                      onChange={(e) =>
                        setScenario({
                          ...scenario,
                          battery_kwh: +e.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    Battery power · kW
                    <input
                      type="number"
                      min="0"
                      max="1000"
                      value={scenario.battery_kw}
                      disabled={mode === "preview"}
                      onChange={(e) =>
                        setScenario({
                          ...scenario,
                          battery_kw: +e.target.value,
                        })
                      }
                    />
                  </label>
                </div>
                <label>
                  Initial battery charge <strong>{pct(scenario.soc0)}</strong>
                  <input
                    type="range"
                    min=".1"
                    max=".95"
                    step=".05"
                    value={scenario.soc0}
                    disabled={mode === "preview"}
                    onChange={(e) =>
                      setScenario({ ...scenario, soc0: +e.target.value })
                    }
                  />
                </label>
                <label>
                  Participating households{" "}
                  <strong>{pct(scenario.flex_participation)}</strong>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step=".05"
                    value={scenario.flex_participation}
                    disabled={mode === "preview"}
                    onChange={(e) =>
                      setScenario({
                        ...scenario,
                        flex_participation: +e.target.value,
                      })
                    }
                  />
                </label>
                <div className="input-grid">
                  <label>
                    Connections
                    <input
                      type="number"
                      min="10"
                      max="600"
                      value={scenario.n_connections}
                      disabled={mode === "preview"}
                      onChange={(e) =>
                        setScenario({
                          ...scenario,
                          n_connections: +e.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    Supply limit · kW
                    <input
                      type="number"
                      min="0"
                      value={scenario.cap_kw}
                      disabled={mode === "preview"}
                      onChange={(e) =>
                        setScenario({ ...scenario, cap_kw: +e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Start hour · IST
                    <input
                      type="number"
                      min="0"
                      max="23.75"
                      step=".25"
                      value={scenario.start_hour}
                      disabled={mode === "preview"}
                      onChange={(e) =>
                        setScenario({
                          ...scenario,
                          start_hour: +e.target.value,
                        })
                      }
                    />
                  </label>
                  <label>
                    End hour · IST
                    <input
                      type="number"
                      min=".25"
                      max="24"
                      step=".25"
                      value={scenario.end_hour}
                      disabled={mode === "preview"}
                      onChange={(e) =>
                        setScenario({ ...scenario, end_hour: +e.target.value })
                      }
                    />
                  </label>
                </div>
                <button
                  className="primary full"
                  disabled={
                    simBusy ||
                    !canRun ||
                    scenario.start_hour < 0 ||
                    scenario.start_hour >= 24 ||
                    scenario.end_hour <= 0 ||
                    scenario.end_hour > 24 ||
                    scenario.start_hour >= scenario.end_hour ||
                    !Number.isInteger(scenario.n_connections) ||
                    scenario.n_connections < 10 ||
                    scenario.n_connections > 600 ||
                    scenario.battery_kwh < 0 ||
                    scenario.battery_kwh > 2000 ||
                    scenario.battery_kw < 0 ||
                    scenario.battery_kw > 1000 ||
                    scenario.cap_kw < 0
                  }
                  onClick={runSimulation}
                >
                  {simBusy ? (
                    <Loader2 className="spin" size={17} />
                  ) : (
                    <Play size={17} />
                  )}{" "}
                  {simBusy
                    ? "Running simulation…"
                    : mode === "preview"
                      ? "Connect to run a scenario"
                      : "Run simulation"}
                </button>
                <p className="muted">
                  {mode === "preview"
                    ? "The saved reference run is shown. Scenario inputs are read-only until connected."
                    : "Runs baseline and coordinated operation on the same modelled day. Results appear when the server completes."}
                </p>
                <State error={simError} />
              </Panel>
              <div className="simulation-results">
                {displayedResult ? (
                  <>
                    <div className="result-heading">
                      <div>
                        <span className="eyebrow">
                          {mode === "preview"
                            ? "SAVED REFERENCE RUN"
                            : "COMPLETED SIMULATION"}
                        </span>
                        <h2>
                          {displayedResult.kpis.jyotiveda
                            .lifeline_availability_in_scarcity >= 0.999
                            ? "Essential power, protected."
                            : "Your scenario, evaluated."}
                        </h2>
                        <p>
                          {String(displayedResult.scenario.transformer_id)} ·{" "}
                          {String(displayedResult.scenario.n_connections)}{" "}
                          connections · {String(displayedResult.scenario.day)}
                        </p>
                      </div>
                      <button className="secondary" onClick={exportResult}>
                        <Download size={16} /> Export JSON
                      </button>
                    </div>
                    <div className="comparison-strip">
                      {[
                        {
                          key: "critical_outage_home_hours",
                          label: "Critical outage",
                          unit: "home-hours",
                        },
                        {
                          key: "lifeline_availability_in_scarcity",
                          label: "Essential availability",
                          unit: "%",
                        },
                        {
                          key: "energy_not_served_kwh",
                          label: "Unmet energy",
                          unit: "kWh",
                        },
                      ].map((m) => (
                        <div key={m.key}>
                          <span>{m.label}</span>
                          <div>
                            <del>
                              {fmt(
                                displayedResult.kpis.baseline[m.key] *
                                  (m.unit === "%" ? 100 : 1),
                              )}
                            </del>
                            <strong>
                              {fmt(
                                displayedResult.kpis.jyotiveda[m.key] *
                                  (m.unit === "%" ? 100 : 1),
                              )}
                            </strong>
                            <small>{m.unit}</small>
                          </div>
                          <p>
                            Baseline <span>→</span> Jyotiveda
                          </p>
                        </div>
                      ))}
                    </div>
                    <Panel
                      title="One day. Two outcomes."
                      sub="Electricity served · kW"
                    >
                      <div className="chart-legend">
                        <span>
                          <i style={{ background: C.energy }} />
                          Jyotiveda
                        </span>
                        <span>
                          <i style={{ background: C.baseline }} />
                          Baseline
                        </span>
                        <span>
                          <i style={{ background: C.muted }} />
                          Original demand
                        </span>
                      </div>
                      <EnergyChart
                        data={displayedResult.timeline.map((t) => ({
                          time: clock(t.ts),
                          baseline: t.baseline.served_kw,
                          jyotiveda: t.jyotiveda.served_kw,
                          demand: t.demand_kw,
                        }))}
                        lines={[
                          {
                            key: "jyotiveda",
                            label: "Jyotiveda served",
                            color: C.energy,
                          },
                          {
                            key: "baseline",
                            label: "Baseline served",
                            color: C.baseline,
                            dash: "5 4",
                          },
                          {
                            key: "demand",
                            label: "Original demand",
                            color: C.muted,
                            dash: "2 3",
                          },
                        ]}
                        marker={clock(
                          displayedResult.timeline[replay]?.ts || "",
                        )}
                      />
                      <div className="replay-controls">
                        <button
                          className="icon-btn"
                          aria-label={playing ? "Pause replay" : "Play replay"}
                          onClick={() => {
                            if (replay >= displayedResult.timeline.length - 1)
                              setReplay(0);
                            setPlaying(!playing);
                          }}
                        >
                          {playing ? <Pause size={19} /> : <Play size={19} />}
                        </button>
                        <strong>
                          {clock(displayedResult.timeline[replay]?.ts || "")}{" "}
                          IST
                        </strong>
                        <input
                          type="range"
                          aria-label="Replay time"
                          min="0"
                          max={displayedResult.timeline.length - 1}
                          value={replay}
                          onChange={(e) => {
                            setPlaying(false);
                            setReplay(+e.target.value);
                          }}
                        />
                        <span>Replay</span>
                      </div>
                      <div className="replay-stats">
                        <span>
                          Battery{" "}
                          <strong>
                            {fmt(
                              displayedResult.timeline[replay]?.jyotiveda
                                .battery_kw,
                            )}{" "}
                            kW
                          </strong>
                        </span>
                        <span>
                          Charge{" "}
                          <strong>
                            {pct(
                              displayedResult.timeline[replay]?.jyotiveda.soc,
                            )}
                          </strong>
                        </span>
                        <span>
                          Homes with essential power{" "}
                          <strong>
                            {
                              displayedResult.timeline[replay]?.jyotiveda
                                .homes_lifeline_ok
                            }
                          </strong>
                        </span>
                      </div>
                    </Panel>
                    <div className="two-col">
                      <Panel
                        title="Baseline vs Jyotiveda"
                        sub="All KPIs returned by this simulation run"
                      >
                        <OutcomeComparison sim={displayedResult} />
                      </Panel>
                      <Panel
                        title="Flexibility market"
                        sub="Fairness-aware clearing for the scarcity window"
                      >
                        {displayedResult.flexibility_market ? (
                          <MarketSummary
                            market={displayedResult.flexibility_market}
                          />
                        ) : (
                          <Empty>No market result in this run.</Empty>
                        )}
                      </Panel>
                    </div>
                    <div className="two-col">
                      <Panel title="How the gap is covered">
                        <BudgetView
                          budget={displayedResult.reliability_budget}
                        />
                      </Panel>
                      <Panel title="Why this decision?">
                        <div className="explanation">
                          <ShieldCheck size={24} />
                          <h3>{displayedResult.explanation.decision}</h3>
                          <p>{displayedResult.explanation.safety}</p>
                        </div>
                        {displayedResult.explanation.drivers.map((d) => (
                          <div className="driver" key={d.feature}>
                            <span>{d.feature}</span>
                            <div>
                              <i
                                style={{ width: `${d.contribution * 100}%` }}
                              />
                            </div>
                            <small>{pct(d.contribution)}</small>
                          </div>
                        ))}
                      </Panel>
                    </div>
                    <Panel
                      title="The day's operating story"
                      sub="Events produced by the simulation"
                    >
                      <ol className="event-timeline">
                        {displayedResult.events.map((e, i) => (
                          <li key={i}>
                            <time>{clock(e.ts)}</time>
                            <div>
                              <strong>{human(e.event)}</strong>
                              <p>{e.message}</p>
                            </div>
                          </li>
                        ))}
                      </ol>
                    </Panel>
                    <div className="two-col">
                      <Metric
                        label="Uninterrupted essential power"
                        value={fmt(
                          displayedResult.kpis.jyotiveda
                            .homes_with_uninterrupted_lifeline,
                          0,
                        )}
                        unit="homes"
                        note={`Baseline: ${fmt(displayedResult.kpis.baseline.homes_with_uninterrupted_lifeline, 0)} homes`}
                      />
                      <Metric
                        label="Served-energy inequality"
                        value={fmt(
                          displayedResult.kpis.jyotiveda
                            .fairness_gini_served_ratio,
                          4,
                        )}
                        note={`Gini: baseline ${fmt(displayedResult.kpis.baseline.fairness_gini_served_ratio, 4)}. Lower is fairer.`}
                      />
                    </div>
                    <p className="method-note">
                      Results apply to this scenario only. Home-hours sum
                      outages across households. Essential-power protection does
                      not mean all appliance demand was met. Baseline uses no
                      storage or flexibility; Jyotiveda uses the configured
                      resources. {displayedResult.id} ·{" "}
                      {fmt(displayedResult.runtime_s, 2)} seconds.
                    </p>
                  </>
                ) : (
                  <Panel title="Ready when you are">
                    <Empty>
                      Choose a scenario and run the model to compare baseline
                      and coordinated operation.
                    </Empty>
                  </Panel>
                )}
              </div>
            </div>
          )}
          {page === "dispatch" && (
            <>
              <div className="action-bar">
                <div>
                  <h2>Safe decisions, visible actions.</h2>
                  <p>
                    All dispatches pass through the backend safety shield.
                    Actions above 60 kW require operator approval in the
                    supplied configuration.
                  </p>
                </div>
                {canDispatch && (
                  <button
                    className="primary"
                    onClick={runCycle}
                    disabled={busy}
                  >
                    <Zap size={17} /> Run reliability cycle
                  </button>
                )}
              </div>
              {cycle?.transformer_id === selected && (
                <Panel
                  title="Control loop · latest cycle"
                  sub="Every stage below is the backend's response to this cycle"
                >
<Pipeline
                    cycle={cycle}
                    running={false}
                    audit={
                      role === "URJA_SAKHI"
                        ? { state: "na" }
                        : auditCheck.isFetching || !auditCheck.data
                          ? { state: "pending" }
                          : { state: auditCheck.data.valid ? "ok" : "bad", records: auditCheck.data.records }
                    }
                  />
                  <ModelRuntime engine={cycle.risk.engine} policy={cycle.policy.policy} />
                </Panel>
              )}
              {lastDispatch && (
                <div className="cc-row safety-dispatch">
                  <Panel title="Safety boundary" sub={`AI proposes · shield decides · ${lastDispatch.id}`}>
                    <SafetyBoundary dispatch={lastDispatch} policy={cycle?.policy} />
                  </Panel>
                  <Panel title="Dispatch lifecycle" sub="Signed command → edge → telemetry verification">
                    <DispatchTimeline dispatch={lastDispatch} />
                  </Panel>
                </div>
              )}
              <State loading={dispatches.isLoading} error={dispatches.error} />
              <Panel
                title="Dispatch queue"
                sub={`${selected} · states reported by the orchestrator`}
              >
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Dispatch</th>
                        <th>Action</th>
                        <th>Status</th>
                        <th>Reason</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {dispatches.data?.map((d) => (
                        <tr key={d.id}>
                          <td className="mono">{d.id}</td>
                          <td>{fmt(d.proposed.battery_kw)} kW battery</td>
                          <td>
                            <Badge text={d.state} />
                          </td>
                          <td>{d.reason}</td>
                          <td>
                            <button
                              className="text-btn"
                              onClick={() => {
                                setDispatch(d);
                                setNote("");
                                setDrawer("dispatch");
                              }}
                            >
                              Inspect
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!dispatches.data?.length && (
                  <Empty>
                    No dispatches for this session. An operator can run a
                    reliability cycle to generate a plan.
                  </Empty>
                )}
              </Panel>
              <div className="two-col">
                <Panel title="Safety pathway">
                  <div className="safety-steps">
                    {[
                      "Forecast & plan",
                      "Deterministic safety shield",
                      "Role check & approval",
                      "Signed command",
                      "Local safety check",
                      "Telemetry verification",
                    ].map((s, i) => (
                      <div key={s}>
                        <span>{i + 1}</span>
                        <strong>{s}</strong>
                      </div>
                    ))}
                  </div>
                </Panel>
                <Panel
                  title="Audit trail"
                  sub="Latest entries in the hash-chained ledger (all transformers)"
                >
                  <State error={audit.error} />
                  {audit.data?.length ? (
                    audit.data.map((a, i) => (
                      <div className="audit-row" key={i}>
                        <time>{clock(String(a.ts))}</time>
                        <div>
                          <strong>{human(String(a.action))}</strong>
                          <small>
                            {String(a.actor)} · {String(a.entity_id)} · #
                            {String(a.seq)}
                          </small>
                        </div>
                      </div>
                    ))
                  ) : (
                    <Empty>No audit entries returned.</Empty>
                  )}
                  <button
                    className="secondary"
                    disabled={mode === "preview" || busy}
                    onClick={async () => {
                      setBusy(true);
                      try {
                        const r = await api<{
                          valid: boolean;
                          records: number;
                        }>("/api/v1/audit/verify");
                        setNotice(
                          `Audit verification: ${r.valid ? "valid" : "invalid"} · ${r.records} records`,
                        );
                      } catch (e) {
                        setNotice((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Verify audit chain
                  </button>
                </Panel>
              </div>
            </>
          )}
          {page === "community" && (
            <>
              <Panel title="Fairness overview" sub="Fairness debt, equity indices and flexibility contributions">
                <FairnessPanel
                  rows={fairRows.rows}
                  rowsLabel={fairRows.label}
                  gini={fair.data?.gini_debt}
                  jain={fair.data?.jain_index}
                  sim={replaySim}
                  market={replaySim?.flexibility_market}
                />
              </Panel>
              <div className="three-metrics">
                <Metric
                  label="Debt inequality"
                  value={fmt(fair.data?.gini_debt, 4)}
                  note="Gini of the fairness-debt ledger"
                />
                <Metric
                  label="Fairness index"
                  value={fmt(fair.data?.jain_index, 4)}
                  note="Jain index on inverse debt · closer to 1 is more equal"
                />
                <Metric
                  label="Household connections"
                  value={fmt(fair.data?.households.length, 0)}
                  note="Select a household to inspect its burden"
                />
              </div>
              <Panel
                title="A fair share of the burden"
                sub="Fairness debt tracks prior shifting and curtailment requests"
              >
                <div className="filters">
                  <input
                    aria-label="Search households"
                    placeholder="Search household ID…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  <select
                    aria-label="Filter fairness level"
                    value={fairFilter}
                    onChange={(e) => setFairFilter(e.target.value)}
                  >
                    <option value="ALL">All burden levels</option>
                    <option>LOW</option>
                    <option>MEDIUM</option>
                    <option>HIGH</option>
                  </select>
                  <div className="chart-legend">
                    <span>
                      <i style={{ background: C.energy }} />
                      Low
                    </span>
                    <span>
                      <i style={{ background: C.warn }} />
                      Medium
                    </span>
                    <span>
                      <i style={{ background: C.danger }} />
                      High
                    </span>
                  </div>
                </div>
                <State loading={fair.isLoading} error={fair.error} />
                <div className="household-grid">
                  {fair.data?.households
                    .filter(
                      (h) =>
                        h.household_id
                          .toLowerCase()
                          .includes(search.toLowerCase()) &&
                        (fairFilter === "ALL" || h.level === fairFilter),
                    )
                    .map((h) => (
                      <button
                        className={`household ${h.level.toLowerCase()}`}
                        key={h.household_id}
                        title={`${h.household_id}: debt ${h.debt}`}
                        onClick={() => {
                          setHouseId(h.household_id);
                          setDrawer("household");
                        }}
                      >
                        <Users size={15} />
                        <span>{h.household_id.split("-").at(-1)}</span>
                      </button>
                    ))}
                </div>
                <p className="chart-note">
                  These are current ledger values, separate from the
                  simulation's served-energy Gini. The supplied runtime does not
                  restore household debt from saved simulations.
                </p>
              </Panel>
              <div className="two-col">
                <Panel title="Community operations">
                  <p className="body-copy">
                    An Urja Sakhi operator can inspect household impact and
                    manage the supported emergency mode for this neighbourhood.
                  </p>
                  <button className="secondary" onClick={() => go("twin")}>
                    View local gateway
                  </button>
                </Panel>
                <Panel title="Affordability, with evidence">
                  <p className="body-copy">
                    The simulation reports resource-allocation costs and
                    flexibility incentives. Ownership, local maintenance and
                    household pricing still need your team's documented business
                    model.
                  </p>
                  <button
                    className="text-btn"
                    disabled={!canRun}
                    onClick={() => go("simulation")}
                  >
                    Inspect simulation costs
                  </button>
                </Panel>
              </div>
            </>
          )}
          <footer>
            <span>
              <img src="/jyotiveda-mark-dark.svg" alt="" width="19" height="19" /> Jyotiveda · Neighbourhood energy reliability
            </span>
            <span>
              {mode === "preview"
                ? "Saved digital-twin output"
                : `Event stream: ${lastEvent || wsState}`}{" "}
              · IST
            </span>
          </footer>
        </main>
        {role !== "ANALYST" && (
          <button
            className="copilot-launch"
            onClick={() => setDrawer("copilot")}
          >
            <Sparkles size={19} />
            <span>Ask Jyoti</span>
          </button>
        )}
      </div>
      {drawer === "connect" && (
        <Drawer title="Connect your backend" onClose={close}>
          <p className="body-copy">
            Preview mode uses saved output from the supplied digital twin.
            Connect your running FastAPI server to run new scenarios and
            operator actions.
          </p>
          <label>
            API base URL
            <input
              type="url"
              value={baseDraft}
              onChange={(e) => setBaseDraft(e.target.value)}
              placeholder="http://localhost:8000"
            />
          </label>
          <label>
            Demo role
            <select
              value={role}
              onChange={(e) => {
                setRole(e.target.value as Role);
                setToken("");
                setMode("preview");
                setPage("overview");
                setChat([]);
              }}
            >
              <option value="DISCOM_OPERATOR">DISCOM operator</option>
              <option value="URJA_SAKHI">Urja Sakhi</option>
              <option value="ANALYST">Analyst</option>
            </select>
          </label>
          <button
            className="primary full"
            onClick={() => connect(true)}
            disabled={busy}
          >
            {busy ? <Loader2 className="spin" size={16} /> : <Zap size={16} />}{" "}
            Connect with local demo login
          </button>
          <p className="muted">
            Uses the backend's development-token endpoint. For local development
            only.
          </p>
          <details>
            <summary>Use an existing bearer token</summary>
            <label>
              Token
              <input
                type="password"
                autoComplete="off"
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
              />
            </label>
            <p className="muted">
              Choose the role matching your token. The server remains
              authoritative.
            </p>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => connect(false)}
            >
              Connect with token
            </button>
          </details>
          {connectError && (
            <div role="alert" className="error">
              {connectError}
            </div>
          )}
          <button
            className="secondary full"
            onClick={() => {
              setMode("preview");
              setToken("");
              setCycle(null);
              setResult(null);
              setChat([]);
              setDrawer(null);
            }}
          >
            Use saved preview
          </button>
          <p className="muted">
            Tokens stay in memory and are cleared on refresh. The backend must
            allow this frontend origin.
          </p>
        </Drawer>
      )}
      {drawer === "household" && (
        <Drawer title={houseId} onClose={close}>
          <Badge text={selectedFair?.level || "UNKNOWN"} />
          <p className="body-copy">
            Fairness debt records the burden this household has already carried.
            Higher debt should reduce repeated requests.
          </p>
          <div className="facts">
            <span>Fairness debt</span>
            <strong>{fmt(selectedFair?.debt, 4)}</strong>
            <span>Demand-response events</span>
            <strong>{fmt(selectedFair?.dr_events, 0)}</strong>
            <span>Energy shifted</span>
            <strong>{fmt(selectedFair?.energy_shifted_kwh)} kWh</strong>
            <span>Energy curtailed</span>
            <strong>{fmt(selectedFair?.energy_curtailed_kwh)} kWh</strong>
          </div>
          <State loading={household.isLoading} error={household.error} />
          {household.data && (
            <div className="notice">
              <strong>{String(household.data.next_flexibility_request)}</strong>
              <p>{String(household.data.reason)}</p>
            </div>
          )}
        </Drawer>
      )}
      {drawer === "dispatch" && dispatch && (
        <Drawer title="Dispatch details" onClose={close}>
          <Badge text={dispatch.state} />
          <h3>{dispatch.id}</h3>
          <p className="body-copy">{dispatch.reason}</p>
          <div className="facts">
            <span>Battery action</span>
            <strong>{fmt(dispatch.proposed.battery_kw)} kW</strong>
            <span>Flexible shift</span>
            <strong>{fmt(dispatch.proposed.flex_shift_kw)} kW</strong>
            <span>Household limits</span>
            <strong>{dispatch.proposed.load_limits}</strong>
          </div>
          {canDispatch && dispatch.state === "AWAITING_APPROVAL" && (
            <>
              <label>
                Operator note
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <div className="button-row">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => decision("approve")}
                >
                  Approve
                </button>
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => decision("reject")}
                >
                  Reject
                </button>
              </div>
            </>
          )}
          <h3 className="drawer-sub">Lifecycle</h3>
          <DispatchLifecycle dispatch={dispatch} />
          <h3 className="drawer-sub">Safety shield</h3>
          <ShieldView shield={dispatch.shield} />
          <details>
            <summary>Safety decision (raw)</summary>
            <pre>{JSON.stringify(dispatch.shield, null, 2)}</pre>
          </details>
          <details>
            <summary>Explanation and history</summary>
            <pre>
              {JSON.stringify(
                {
                  explanation: dispatch.explanation,
                  history: dispatch.history,
                },
                null,
                2,
              )}
            </pre>
          </details>
          {notice && (
            <div role="status" className="notice">
              {notice}
            </div>
          )}
        </Drawer>
      )}
      {drawer === "copilot" && (
        <Drawer title="Jyoti Copilot" onClose={close}>
          <div className="copilot-context">
            <Sparkles size={22} />
            <div>
              <strong>A little clarity for your grid.</strong>
              <p>
                {row?.ward} · {selected}
              </p>
            </div>
          </div>
          {mode === "preview" && (
            <div className="notice">
              Connect your backend to use Jyoti. No AI model is active in this
              preview.
            </div>
          )}
          <div className="suggestions">
            {[
              "Why is this neighbourhood at risk?",
              "Explain this reliability plan.",
              "Which actions need my approval?",
            ].map((s) => (
              <button
                disabled={chatBusy || mode === "preview"}
                key={s}
                onClick={() => send(s)}
              >
                {s}
              </button>
            ))}
          </div>
          <div className="chat-messages" aria-live="polite">
            {chat.map((m, i) => (
              <div key={i} className={`chat-message ${m.role}`}>
                <small>
                  {m.role === "user"
                    ? "You"
                    : `Jyoti · ${m.mode || "response"}`}
                </small>
                <p>{m.content}</p>
              </div>
            ))}
            {chatBusy && <Loader2 className="spin" />}
          </div>
          <form
            className="chat-input"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <input
              aria-label="Message Jyoti"
              placeholder="Ask about this neighbourhood…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              maxLength={4000}
              disabled={mode === "preview"}
            />
            <button
              className="primary"
              aria-label="Send message"
              disabled={chatBusy || !message.trim() || mode === "preview"}
            >
              <Send size={18} />
            </button>
          </form>
          {notice && <p role="status">{notice}</p>}
        </Drawer>
      )}
    </div>
  );
}
function ClockWindow({ data }: { data: Record<string, unknown> }) {
  return (
    <>
      {Object.entries(data).map(([k, v]) => (
        <div key={k}>
          <span>{human(k)}</span>
          <strong>
            {typeof v === "number"
              ? fmt(v, 2)
              : typeof v === "string" && v.includes("T")
                ? clock(v) + " IST"
                : String(v)}
          </strong>
        </div>
      ))}
    </>
  );
}
