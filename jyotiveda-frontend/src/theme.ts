// Single source for colours used inside SVG / Recharts (CSS uses the matching custom properties).
export const C = {
  bg: "#070b0d",
  panel: "#0c1316",
  line: "rgba(140,175,165,0.14)",
  grid: "rgba(140,175,165,0.09)",
  text: "#e4ece8",
  text2: "#b5c4be",
  muted: "#7d918a",
  dim: "#4f625b",
  energy: "#2fd4a7",
  cyan: "#4cc3ff",
  solar: "#f5b53d",
  battery: "#9aa6ff",
  flex: "#d9a066",
  warn: "#f2a33a",
  danger: "#ff6a55",
  baseline: "#8c7a6b",
} as const;

export const RISK_COLOR: Record<string, string> = {
  LOW: C.energy,
  MODERATE: "#d8c25a",
  HIGH: C.warn,
  CRITICAL: C.danger,
};
export const riskColor = (level?: string) =>
  (level && RISK_COLOR[level.toUpperCase()]) || C.muted;

export const RESOURCE: Record<string, { label: string; color: string; hint: string }> = {
  p2p_solar: {
    label: "P2P rooftop solar",
    color: C.solar,
    hint: "Rooftop surplus shared inside the transformer area",
  },
  battery: {
    label: "Community battery",
    color: C.battery,
    hint: "Shared second-life LFP, degradation-priced",
  },
  flexibility_market: {
    label: "Flexibility market",
    color: C.energy,
    hint: "Household offers cleared by the fairness-aware MILP",
  },
  auto_load_shift: {
    label: "Automatic load shift",
    color: C.cyan,
    hint: "Opt-in smart-plug / smart-meter scheduling",
  },
  lifeline_mode_curtailment: {
    label: "Lifeline-mode curtailment",
    color: C.flex,
    hint: "Comfort loads limited; lifeline loads always served",
  },
};

export const chartAxis = { fontSize: 11, fill: C.muted, fontFamily: "JetBrains Mono, ui-monospace, monospace" };
export const tooltipStyle = {
  background: "#0f181c",
  border: `1px solid ${C.line}`,
  borderRadius: 6,
  fontSize: 12,
  color: C.text,
};
