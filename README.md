# Jyotiveda

**AI-powered, transformer-scale reliability operating system for distribution grids**

Jyotiveda keeps essential electricity on in a distribution-transformer neighbourhood when renewable output drops
and upstream supply is capped. It forecasts the gap, decides how to cover it with finite local resources, and
sends only safety-validated, signed commands to the edge.

This repository holds two applications that are developed and versioned together:

```
jyotiveda/
├── jyotiveda-frontend/   React 19 + TypeScript + Vite command centre
└── jyotiveda backend/    FastAPI + Python 3.11+ reliability engine and digital twin
```

> **Status labels used in this README**
> **IMPLEMENTED**: code exists and runs in the local demo · **SIMULATED**: produced by the digital twin, not field data ·
> **FALLBACK**: a simpler engine runs because the primary one is unavailable · **NOT TRAINED**: architecture exists, no
> trained weights are in the repo · **NOT VERIFIED**: described in code or docs but not exercised in this repo's tests or demo.

---

## 1. Problem

Indian low-voltage neighbourhoods increasingly depend on rooftop solar. When clouds cut solar output in the
evening ramp, and the DISCOM caps upstream supply in the same window, the usual response is blunt load-shedding:
whole homes go dark, including lifeline loads (lights, fans, phone charging, medical devices), and the same
households tend to carry the burden repeatedly.

## 2. Core concept

Treat reliability as a **finite budget** to be allocated, not a switch to be thrown:

1. Forecast the **Reliability Gap** probabilistically (P10/P50/P90).
2. Build a **Reliability Budget**: cover the gap in merit order from P2P solar, the community battery, a
   fairness-aware flexibility market and automatic load shifting, using lifeline-mode curtailment only last.
3. Plan with risk-aware **CVaR-MPC**; pass every proposed action through a deterministic **Safety Shield**.
4. Dispatch **signed** commands, verify them against telemetry, record everything in a **hash-chained audit**.
5. Track **Fairness Debt** so the same households are not asked to flex every time.

## 3. Architecture

```
Telemetry → Forecast → Grid risk → Reliability gap → Reliability Budget → Flexibility market
        → CVaR-MPC → Safety Shield → Dispatch (Ed25519-signed) → Edge gateway → Telemetry verification
        → Fairness debt + audit ledger
```

The backend is a modular monolith (`JYOTIVEDA_ROLE=all` for local use) with an in-memory event bus and SQLite.
Kafka, TimescaleDB, Keycloak/OIDC, Helm and Terraform material is present under `jyotiveda backend/`
(`docker-compose.yml`, `deploy/`, `infra/`) but is **NOT VERIFIED** in this repository's demo or tests.

## 4. Frontend (`jyotiveda-frontend/`)

- React 19, TypeScript (strict), Vite 6, TanStack Query, Recharts, lucide-react. **IMPLEMENTED**
- One API client (`src/api.ts`); base URL from `VITE_API_BASE_URL` (see `.env.example`).
- Pages: command centre (overview), neighbourhood twin, forecasts & reliability, simulation lab,
  dispatch & safety, community & fairness, plus the Jyoti Copilot drawer.
- Live updates over the backend WebSocket `/ws/live` (CloudEvents), with connection states shown in the status strip.
- **Preview mode** shows a saved backend snapshot (`public/preview-data.json`), clearly labelled; every
  model-changing control requires a live backend connection.
- Every operational number is read from a backend response; static text is explanatory only.

## 5. Backend (`jyotiveda backend/`)

- FastAPI app factory `jyotiveda.api.app:create_app`, 33 REST routes under `/api/v1`, `/healthz`, `/readyz`,
  `/metrics` (Prometheus), `/openapi.json`, WebSocket `/ws/live`. **IMPLEMENTED**
- Dev authentication: `POST /api/v1/auth/dev-token` mints an HS256 JWT (only when `JYOTIVEDA_AUTH_MODE=dev`
  and not in staging/prod). OIDC mode exists in code and is **NOT VERIFIED** here.
- RBAC roles used by the UI: `DISCOM_OPERATOR`, `URJA_SAKHI`, `ANALYST`.
- CORS: explicit origins `http://localhost:3000`, `http://127.0.0.1:3000` (`config.py`, override with
  `JYOTIVEDA_CORS_ORIGINS`).
- See `jyotiveda backend/README.md` and `docs/ARCHITECTURE.md` for the full design. Those documents describe the
  intended production architecture; the runtime state verified in this repo is listed in section 13.

## 6. Main control loop

`POST /api/v1/reliability/{dt}/cycle` runs one pass and returns every stage's result:
**OBSERVE** (twin slot, edge mode) → **PREDICT** (forecast backend + accuracy metadata) → **UNDERSTAND**
(grid risk, gap) → **ALLOCATE** (budget + market) → **ORCHESTRATE** (MPC plan → shield → dispatch) →
**SHARE & LEARN** (fairness, audit). **IMPLEMENTED**; operates on the digital twin (**SIMULATED** grid).

## 7. Reliability Budget

`reliability/budget.py` turns the P90 gap into required kWh for the scarcity window and allocates it in merit
order: P2P rooftop solar → community battery (degradation-priced) → flexibility market → automatic load shift →
lifeline-mode curtailment; any remainder is reported as **uncovered**. **IMPLEMENTED**, values are **SIMULATED**.

## 8. Fairness Debt

`fairness/debt.py` keeps a decaying per-household burden ledger. It weights the flexibility market and
curtailment so recently burdened households are deferred, and reports Gini and Jain indices
(`GET /api/v1/fairness/{dt}`). **IMPLEMENTED**. Household data is synthetic (**SIMULATED**).

## 9. Safety Shield

`safety/shield.py`: deterministic rules S1–S10 (command freshness, telemetry freshness, battery power, SoC
envelope, ramp, transformer rating, voltage ±6 %, lifeline protection, blast radius, protection alarms). It
returns APPROVED, MODIFIED (clipped to a safe envelope) or REJECTED. Optimisers cannot reach devices directly.
**IMPLEMENTED**; property-tested in `tests/test_safety_shield.py`.

## 10. Digital Twin

`twin/` simulates a transformer neighbourhood (reference fleet of 8 DTs around Pune, e.g. DT-104 with 180
connections, 200 kWh battery). The renewable-shock endpoint runs a full counterfactual day:
**baseline** (no storage/flexibility/coordination) vs **Jyotiveda**, on the same modelled physics. **SIMULATED**:
these are scenario results, not field measurements or real-world guarantees.

## 11. Dispatch

`dispatch/orchestrator.py`: PROPOSED → VALIDATING → (AWAITING_APPROVAL) → APPROVED → SENT → ACKNOWLEDGED →
EXECUTED → VERIFIED | FAILED. Actions above 60 kW (configurable `JYOTIVEDA_AUTO_APPROVE_MAX_KW`) or wide
curtailment wait for an operator. Commands are Ed25519-signed (an **ephemeral dev key** is generated when no key
path is configured) and verified against measured battery power (±10 % / 3 kW). **IMPLEMENTED** on the twin.

## 12. Edge resilience

`edge/gateway.py` models the edge gateway with modes CLOUD_COORDINATED → AUTONOMOUS_CACHED_PLAN →
AUTONOMOUS_LIFELINE, plus MANUAL_EMERGENCY for the Urja Sakhi operator. `POST /api/v1/edge/{dt}/simulate-outage`
cuts the cloud link in the twin. **IMPLEMENTED / SIMULATED**; no physical gateway, MQTT broker or device was tested.

## 13. Current model runtime (verified)

| Component | Status | Runtime in the local demo |
|---|---|---|
| Demand forecasting | IMPLEMENTED | `lightgbm-quantile` (reported by the forecast metadata). Chronos-2 needs the `ml` extra: NOT VERIFIED |
| Solar forecasting | IMPLEMENTED | `clear-sky x weather-ai` (synthetic weather in the twin) |
| Grid risk | IMPLEMENTED, FALLBACK | **Physics / LinDistFlow** (`engine: physics-lindistflow`) |
| GNN (GraphSAGE + GATv2) | IMPLEMENTED, **NOT TRAINED** | No checkpoint in the repo; physics engine is used |
| Optimisation | IMPLEMENTED | **CVaR-MPC** with the **CLARABEL** solver |
| PPO / RL residual policy | IMPLEMENTED, **NOT TRAINED** | No ONNX policy in the repo; **MPC runs alone** (`policy: mpc-only`) |
| Jyoti Copilot | IMPLEMENTED, FALLBACK | **Offline** intent router unless `JYOTIVEDA_ANTHROPIC_API_KEY` is set (the UI shows `Jyoti · offline`) |

The UI's "Model runtime" panel and status strip read these values from the backend and never claim GNN or RL
inference is active.

## 14. Local setup

Prerequisites: Python ≥ 3.11 (3.12 tested), Node.js ≥ 20 (22 tested), `uv` or `pip`.

```bash
# Backend
cd "jyotiveda backend"
uv venv && uv pip install -e ".[dev]"      # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
source .venv/bin/activate
make demo                                   # fresh demo DB in ./.demo, API on http://localhost:8000
#   (or: jyotiveda serve --port 8000        # keeps ./jyotiveda.db history)

# Frontend (second terminal)
cd jyotiveda-frontend
npm ci
npm run dev                                 # http://localhost:3000
```

Open **http://localhost:3000** → *Connect backend* → API base URL `http://localhost:8000` →
*Connect with local demo login*. Optional: copy `.env.example` to `.env` in either app to change settings.
Never put real secrets in the frontend `.env`; only `VITE_*` values reach the browser.

## 15. Testing

```bash
# Backend
cd "jyotiveda backend" && source .venv/bin/activate
pytest                                       # 26 tests
ruff check src tests ml && ruff format --check src tests ml

# Frontend
cd jyotiveda-frontend
npm run build                                # tsc -b (strict) + vite build
npx playwright install chromium              # once
node qa.mjs                                  # preview-mode browser QA (serves on :3000)
node qa-connected.mjs                        # connected QA; needs a DISPOSABLE backend on http://127.0.0.1:8000
# macOS with system Chrome instead of Playwright's Chromium:
# CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" node qa-connected.mjs
```

`qa-connected.mjs` mutates the backend (simulations, dispatches, clock, edge mode). Run it against a fresh
`make demo` backend, and restart the backend before a live presentation. Port 3000 must be free.

## 16. Demo flow

See **[DEMO.md](DEMO.md)** for the step-by-step live sequence.

## 17. Current limitations

- All grid, household and weather data come from the **digital twin** (SIMULATED). No field data, devices,
  smart meters or DISCOM systems are connected.
- GNN and PPO models are **NOT TRAINED**; the runtime uses the physics risk engine and MPC only.
- Copilot is offline without an Anthropic API key.
- The live twin view reports the **net import need** (demand − solar − battery) before any curtailment; during
  the evening supply cap this can exceed the cap. The UI labels this "import need" and shows the excess; the
  simulator's replay shows the dispatched import, which respects the cap.
- Backend runtime state (twin clock, edge mode, dispatches, audit chain, fairness ledger) is in memory and
  resets on restart; simulation history and audit/dispatch rows persist in the SQLite file
  (`make demo` starts with a fresh file).
- Production components (Kafka, TimescaleDB, Keycloak/OIDC, Helm, Terraform, CI pipeline) are present but
  **NOT VERIFIED** here. Development credentials in `docker-compose.yml` / `deploy/` are placeholders.
- The frontend loads Inter / JetBrains Mono from Google Fonts; offline it falls back to system fonts.
- The production JS bundle is ~800 kB (≈230 kB gzip), mostly charting; it is not code-split.
- Tested in headless Chromium at 1920×1080, 1366×768 and 390×844; Safari, Firefox and physical phones were not tested.
