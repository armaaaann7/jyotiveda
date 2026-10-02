import { useEffect, useRef, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ComposedChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ReferenceLine,
} from "recharts";
import {
  X,
  AlertCircle,
  Loader2,
  Zap,
  Sun,
  BatteryCharging,
  ShieldCheck,
} from "lucide-react";
import type { Budget, FleetRow, Topology } from "./types";
import { C, RESOURCE, chartAxis, tooltipStyle } from "./theme";
export const fmt = (v: number | null | undefined, d = 1) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : v.toLocaleString("en-IN", { maximumFractionDigits: d });
export const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${fmt(v * 100)}%`;
export const clock = (s: string) => (s ? s.slice(11, 16) : "—");
export const human = (s: string) =>
  s
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/^./, (x) => x.toUpperCase());
export function Badge({ text }: { text: string }) {
  return (
    <span className={`badge ${text.toLowerCase().replaceAll(" ", "-")}`}>
      {human(text)}
    </span>
  );
}
export function Panel({
  title,
  sub,
  action,
  children,
  className = "",
}: {
  title?: string;
  sub?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-head">
          <div>
            <h2>{title}</h2>
            {sub && <p>{sub}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Metric({
  label,
  value,
  unit,
  note,
  icon,
}: {
  label: string;
  value: string;
  unit?: string;
  note: string;
  icon?: ReactNode;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {label}
        {icon}
      </div>
      <div className="metric-value">
        {value}
        <small>{unit}</small>
      </div>
      <p>{note}</p>
    </div>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <Zap size={26} />
      <p>{children}</p>
    </div>
  );
}
export function State({
  loading,
  error,
  onRetry,
}: {
  loading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
}) {
  return loading ? (
    <div className="state" role="status">
      <Loader2 className="spin" size={19} /> Loading neighbourhood data…
    </div>
  ) : error ? (
    <div className="error" role="alert" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <AlertCircle size={20} />
        <span>{error.message}</span>
      </div>
      {onRetry && (
        <button className="secondary" onClick={onRetry} style={{ padding: "0.25rem 0.75rem", fontSize: "0.85rem" }}>
          Retry
        </button>
      )}
    </div>
  ) : null;
}
export function Drawer({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const root = ref.current;
    root?.querySelector<HTMLButtonElement>("button")?.focus();
    const listener = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && root) {
        const els = [
          ...root.querySelectorAll<HTMLElement>(
            "button:not([disabled]),input,select,textarea,a[href]",
          ),
        ];
        const first = els[0],
          last = els.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", listener);
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", listener);
      document.body.style.overflow = old;
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="drawer-head">
          <h2>{title}</h2>
          <button
            className="icon-btn"
            aria-label="Close panel"
            onClick={onClose}
          >
            <X />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
export function EnergyChart({
  data,
  lines,
  height = 260,
  marker,
}: {
  data: Record<string, unknown>[];
  lines: { key: string; label: string; color: string; dash?: string }[];
  height?: number;
  marker?: string;
}) {
  return (
    <div
      style={{ height, width: "100%" }}
      role="img"
      aria-label={
        lines.map((l) => l.label).join(", ") + " in kilowatts over time"
      }
    >
      <ResponsiveContainer>
        <ComposedChart
          data={data}
          margin={{ left: 0, right: 15, top: 12, bottom: 0 }}
        >
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis
            dataKey="time"
            tick={chartAxis}
            axisLine={false}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            tick={chartAxis}
            axisLine={false}
            tickLine={false}
            width={44}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(v: number, n: string) => [`${fmt(v)} kW`, n]}
          />
          {lines.map((l, i) =>
            i === 0 ? (
              <Area
                key={l.key}
                type="monotone"
                dataKey={l.key}
                name={l.label}
                fill={l.color}
                fillOpacity={0.07}
                stroke={l.color}
                strokeWidth={2}
                isAnimationActive={false}
              />
            ) : (
              <Line
                key={l.key}
                type="monotone"
                dataKey={l.key}
                name={l.label}
                stroke={l.color}
                strokeWidth={2}
                strokeDasharray={l.dash}
                dot={false}
                isAnimationActive={false}
              />
            ),
          )}
          {marker && (
            <ReferenceLine x={marker} stroke={C.text2} strokeDasharray="4 4" />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
export function FanChart({
  data,
}: {
  data: { time: string; p10: number; p50: number; p90: number }[];
}) {
  return (
    <div
      style={{ height: 265 }}
      role="img"
      aria-label="Demand forecast median and P10 to P90 uncertainty band"
    >
      <ResponsiveContainer>
        <AreaChart data={data.map((d) => ({ ...d, band: [d.p10, d.p90] }))}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis
            dataKey="time"
            minTickGap={50}
            axisLine={false}
            tickLine={false}
            tick={chartAxis}
          />
          <YAxis
            width={42}
            axisLine={false}
            tickLine={false}
            tick={chartAxis}
          />
          <Tooltip contentStyle={tooltipStyle} />
          <Area
            dataKey="band"
            stroke="none"
            fill={C.energy}
            fillOpacity={0.14}
            name="P10–P90 (kW)"
            isAnimationActive={false}
          />
          <Area
            dataKey="p50"
            fill="transparent"
            stroke={C.energy}
            strokeWidth={2}
            name="P50 (kW)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
const resourceNames: Record<string, string> = {
  p2p_solar: "Local solar sharing",
  battery: "Community battery",
  flexibility_market: "Flexible demand",
  auto_load_shift: "Scheduled loads",
  lifeline_mode_curtailment: "Comfort-load limits",
};
export function BudgetView({ budget }: { budget?: Budget }) {
  return !budget ? (
    <Empty>
      Run a simulation or an operator cycle to create a reliability budget.
    </Empty>
  ) : (
    <>
      <div className="budget-total">
        <span>Reliability requirement</span>
        <strong>
          {fmt(budget.required_kwh)} <small>kWh</small>
        </strong>
      </div>
      <div className="budget-stack">
        {budget.allocation.map((a, i) => (
          <div
            key={a.resource}
            style={{
              flex: a.energy_kwh,
              background: RESOURCE[a.resource]?.color || C.muted,
            }}
            title={`${resourceNames[a.resource] || human(a.resource)}: ${fmt(a.energy_kwh)} kWh`}
          />
        ))}
      </div>
      {budget.allocation.map((a, i) => (
        <div className="budget-row" key={a.resource}>
          <span>
            <i
              style={{
                background: RESOURCE[a.resource]?.color || C.muted,
              }}
            />
            {resourceNames[a.resource] || human(a.resource)}
          </span>
          <strong>
            {fmt(a.energy_kwh)} <small>kWh</small>
          </strong>
        </div>
      ))}
      <div className="budget-row">
        <span>Uncovered requirement</span>
        <strong>{fmt(budget.uncovered_kwh)} kWh</strong>
      </div>
      <div className="budget-foot">
        Modelled allocation cost{" "}
        <strong>₹{fmt(budget.expected_cost_inr, 0)}</strong>
        <p>Includes modelled resource costs; not a household bill.</p>
      </div>
    </>
  );
}
export function Network({
  row,
  topology,
  onHousehold,
}: {
  row: FleetRow;
  topology?: Topology;
  onHousehold?: (id: string) => void;
}) {
  const buses = topology?.nodes.filter((n) => n.kind === "bus") || [];
  const homes = topology?.nodes.filter((n) => n.kind === "household") || [];
  const branches = buses.length
    ? buses.slice(0, 12)
    : Array.from({ length: 6 }, (_, i) => ({
        id: `B${i + 1}`,
        bus: i + 1,
        kind: "bus",
      }));
  return (
    <div className="network">
      <div className="network-meta">
        <span>NEIGHBOURHOOD SCHEMATIC</span>
        <span>
          {topology ? `${homes.length} connections` : "Resource overview"}
        </span>
      </div>
      <svg
        viewBox="0 0 820 365"
        role="img"
        aria-label="Schematic showing transformer, solar, storage and household connections"
      >
        <defs>
          <pattern
            id="grid"
            width="22"
            height="22"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r=".7" fill="rgba(140,175,165,.22)" />
          </pattern>
        </defs>
        <rect width="820" height="365" fill="url(#grid)" />
        <path
          d="M125 98H408V172 M690 98H408 M408 201V249H95 M408 249H735"
          className="flow-line"
        />
        <rect
          x="45"
          y="58"
          width="160"
          height="74"
          rx="10"
          fill={C.panel}
          stroke={C.line}
        />
        <text x="65" y="84" className="svg-label">
          ROOFTOP SOLAR
        </text>
        <text x="65" y="111" className="svg-value">
          {fmt(row.solar_kw)} kW
        </text>
        <rect
          x="610"
          y="58"
          width="166"
          height="74"
          rx="10"
          fill={C.panel}
          stroke={C.line}
        />
        <text x="630" y="84" className="svg-label">
          SHARED STORAGE
        </text>
        <text x="630" y="111" className="svg-value">
          {row.battery.capacity_kwh > 0
            ? pct(row.battery.soc)
            : "Not installed"}
        </text>
        <rect x="300" y="145" width="216" height="64" rx="10" fill="#0d2621" stroke={C.energy} strokeOpacity={0.5} />
        <text x="326" y="170" fill={C.energy} fontSize="11" letterSpacing="1.5">
          DISTRIBUTION TRANSFORMER
        </text>
        <text x="326" y="193" fill={C.text} fontSize="18" fontWeight="600">
          {row.transformer_id}
          <tspan dx="20" fontSize="13">
            {fmt(row.loading_pct)}% load
          </tspan>
        </text>
        {branches.map((b, i) => {
          const x = 95 + (i * 640) / Math.max(branches.length - 1, 1);
          const members = homes.filter((h) => h.bus === b.bus);
          return (
            <g key={b.id}>
              <path
                d={`M${x} 249v27`}
                fill="none"
                stroke={C.dim}
                strokeWidth="2"
              />
              <circle cx={x} cy="249" r="4" fill={C.energy} />
              <text x={x} y="277" textAnchor="middle" className="svg-label">
                {b.id}
              </text>
              {members.length ? (
                members.map((h, j) => (
                  <g
                    key={h.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Inspect household ${h.id}`}
                    onClick={() => onHousehold?.(h.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ")
                        onHousehold?.(h.id);
                    }}
                  >
                    <title>{h.id}</title>
                    <rect
                      className="house-dot"
                      x={x - 27 + (j % 6) * 10}
                      y={289 + Math.floor(j / 6) * 10}
                      width="7"
                      height="7"
                      rx="2"
                      fill={C.energy}
                      fillOpacity={0.55}
                    />
                  </g>
                ))
              ) : (
                <g>
                  <rect
                    x={x - 18}
                    y="289"
                    width="36"
                    height="25"
                    rx="4"
                    fill="rgba(140,175,165,.15)"
                  />
                  <text
                    x={x}
                    y="334"
                    textAnchor="middle"
                    fontSize="11"
                    fill={C.muted}
                  >
                    Loads
                  </text>
                </g>
              )}
            </g>
          );
        })}
        <path
          d="M409 20v115"
          stroke={C.dim}
          strokeWidth="2"
          strokeDasharray="5 5"
        />
        <text x="425" y="40" className="svg-label">
          GRID LIMIT {fmt(row.grid_cap_kw)} kW
        </text>
      </svg>
      <div className="network-legend">
        <span>
          <Sun size={14} /> Solar
        </span>
        <span>
          <Zap size={14} /> Distribution
        </span>
        <span>
          <BatteryCharging size={14} /> Storage
        </span>
        <span>
          <ShieldCheck size={14} /> Essential demand {fmt(row.protected_kw)} kW
        </span>
      </div>
    </div>
  );
}
