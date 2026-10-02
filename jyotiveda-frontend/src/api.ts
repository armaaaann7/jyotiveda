import type {
  Role,
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
  AuditRecord,
  AuditVerifyResult,
  FlexOffer,
  FlexResult,
  CopilotResponse,
  DevTokenResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Vite only exposes VITE_* variables to the browser bundle.
export const getApiBaseUrl = (): string =>
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export async function request<T>(
  base: string,
  token: string,
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);

  try {
    const url = base.replace(/\/$/, "") + path;
    const httpMethod = method || (body !== undefined ? "POST" : "GET");
    const headers: Record<string, string> = {};

    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    const response = await fetch(url, {
      method: httpMethod,
      signal: controller.signal,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      let detailMsg = response.statusText;
      let rawDetail: unknown = null;
      try {
        const errJson = await response.json();
        rawDetail = errJson.detail || errJson;
        if (typeof errJson.detail === "string") {
          detailMsg = errJson.detail;
        } else if (errJson.detail) {
          detailMsg = JSON.stringify(errJson.detail);
        } else if (errJson.message) {
          detailMsg = errJson.message;
        }
      } catch {
        // ignore error json parse failures
      }

      let humanMsg = `${response.status}: ${detailMsg}`;
      if (response.status === 401) {
        humanMsg = "Authentication token invalid or expired. Please sign in again.";
      } else if (response.status === 403) {
        humanMsg = `Permission denied (403): ${detailMsg}`;
      } else if (response.status === 404) {
        humanMsg = `Resource not found (404): ${detailMsg}`;
      } else if (response.status === 409) {
        humanMsg = `State conflict (409): ${detailMsg}`;
      } else if (response.status === 422) {
        humanMsg = `Validation failed (422): ${detailMsg}`;
      } else if (response.status === 429) {
        humanMsg = "Too many requests. Please slow down.";
      } else if (response.status >= 500) {
        humanMsg = `Jyotiveda backend error (${response.status}): ${detailMsg}`;
      }

      throw new ApiError(response.status, humanMsg, rawDetail);
    }

    return (await response.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof Error && e.name === "AbortError") {
      throw new Error("Request timed out. Check the backend terminal.");
    }
    throw new Error(
      "Jyotiveda backend is temporarily unavailable. Check the URL, backend server, and CORS configuration.",
    );
  } finally {
    clearTimeout(timer);
  }
}

// ----------------------------------------------------------------------------- Centralized Typed API Services
export const apiServices = {
  auth: {
    getDevToken: (base: string, sub: string, role: Role, transformers: string[] = [], household_id?: string) =>
      request<DevTokenResponse>(base, "", "/api/v1/auth/dev-token", { sub, role, transformers, household_id }),
  },
  transformers: {
    list: (base: string, token: string) =>
      request<{ count: number; transformers: FleetRow[]; totals: { households: number; renewable_share: number } }>(
        base,
        token,
        "/api/v1/transformers",
      ),
    get: (base: string, token: string, dtId: string) =>
      request<Record<string, unknown>>(base, token, `/api/v1/transformers/${dtId}`),
    topology: (base: string, token: string, dtId: string) =>
      request<Topology>(base, token, `/api/v1/transformers/${dtId}/topology`),
    households: (base: string, token: string, dtId: string, offset = 0, limit = 50) =>
      request<{ total: number; offset: number; items: Record<string, unknown>[] }>(
        base,
        token,
        `/api/v1/transformers/${dtId}/households?offset=${offset}&limit=${limit}`,
      ),
    setClock: (base: string, token: string, dtId: string, hour: number) =>
      request<Record<string, unknown>>(base, token, `/api/v1/transformers/${dtId}/clock`, { hour }),
  },
  forecast: {
    get: (base: string, token: string, dtId: string, kind = "all") =>
      request<Forecast>(base, token, `/api/v1/forecast/${dtId}?kind=${kind}`),
  },
  reliability: {
    getRisk: (base: string, token: string, dtId: string) =>
      request<GridRisk>(base, token, `/api/v1/reliability/${dtId}/risk`),
    runCycle: (base: string, token: string, dtId: string) =>
      request<Cycle>(base, token, `/api/v1/reliability/${dtId}/cycle`, {}),
  },
  flexibility: {
    listOffers: (base: string, token: string, transformerId?: string) =>
      request<FlexOffer[]>(
        base,
        token,
        `/api/v1/flexibility/offers${transformerId ? `?transformer_id=${transformerId}` : ""}`,
      ),
    postOffer: (base: string, token: string, offer: FlexOffer) =>
      request<{ id: string; status: string; price_per_kwh: number }>(base, token, "/api/v1/flexibility/offers", offer),
    clear: (base: string, token: string, dtId: string, need_kwh: number, fairness_lambda = 4.0, price_cap_inr_per_kwh = 15.0) =>
      request<FlexResult>(base, token, `/api/v1/flexibility/${dtId}/clear`, {
        need_kwh,
        fairness_lambda,
        price_cap_inr_per_kwh,
      }),
  },
  fairness: {
    get: (base: string, token: string, dtId: string) =>
      request<Fairness>(base, token, `/api/v1/fairness/${dtId}`),
    getHousehold: (base: string, token: string, dtId: string, hhId: string) =>
      request<Record<string, unknown>>(base, token, `/api/v1/fairness/${dtId}/households/${hhId}`),
  },
  simulation: {
    run: (base: string, token: string, spec: Record<string, unknown>) =>
      request<Simulation>(base, token, "/api/v1/simulation/run", spec),
    renewableShock: (base: string, token: string, dtId: string, solarReduction = 0.6) =>
      request<Simulation>(
        base,
        token,
        `/api/v1/simulation/scenario/renewable-shock?solar_reduction=${solarReduction}&transformer_id=${dtId}`,
        {},
      ),
    list: (base: string, token: string) =>
      request<Record<string, unknown>[]>(base, token, "/api/v1/simulation"),
    get: (base: string, token: string, simId: string, series = false) =>
      request<Simulation>(base, token, `/api/v1/simulation/${simId}?series=${series}`),
  },
  dispatch: {
    list: (base: string, token: string, transformerId?: string) =>
      request<Dispatch[]>(
        base,
        token,
        `/api/v1/dispatch${transformerId ? `?transformer_id=${transformerId}` : ""}`,
      ),
    get: (base: string, token: string, dispatchId: string) =>
      request<Dispatch>(base, token, `/api/v1/dispatch/${dispatchId}`),
    approve: (base: string, token: string, dispatchId: string, note = "") =>
      request<Dispatch>(base, token, `/api/v1/dispatch/${dispatchId}/approve`, { note }),
    reject: (base: string, token: string, dispatchId: string, note = "") =>
      request<Dispatch>(base, token, `/api/v1/dispatch/${dispatchId}/reject`, { note }),
    emergency: (base: string, token: string, dtId: string, enable = true) =>
      request<Record<string, unknown>>(base, token, `/api/v1/dispatch/emergency/${dtId}?enable=${enable}`, {}),
  },
  edge: {
    getStatus: (base: string, token: string, dtId: string) =>
      request<Edge>(base, token, `/api/v1/edge/${dtId}`),
    simulateOutage: (base: string, token: string, dtId: string, seconds = 300) =>
      request<OutageResult>(base, token, `/api/v1/edge/${dtId}/simulate-outage?seconds=${seconds}`, {}),
  },
  audit: {
    getLogs: (base: string, token: string, entityId?: string, limit = 15) =>
      request<AuditRecord[]>(
        base,
        token,
        `/api/v1/audit${entityId ? `?entity_id=${entityId}&limit=${limit}` : `?limit=${limit}`}`,
      ),
    verify: (base: string, token: string) =>
      request<AuditVerifyResult>(base, token, "/api/v1/audit/verify"),
  },
  copilot: {
    chat: (base: string, token: string, message: string, transformerId?: string, history: Record<string, unknown>[] = []) =>
      request<CopilotResponse>(base, token, "/api/v1/copilot/chat", {
        message,
        transformer_id: transformerId,
        history,
      }),
  },
  analytics: {
    getSummary: (base: string, token: string) =>
      request<Summary>(base, token, "/api/v1/analytics/summary"),
    getVersion: (base: string, token: string) =>
      request<Record<string, unknown>>(base, token, "/api/v1/version"),
  },
};
