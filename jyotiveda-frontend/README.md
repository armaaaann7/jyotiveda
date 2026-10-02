# Jyotiveda frontend

A React + TypeScript energy reliability command centre for the supplied Jyotiveda FastAPI backend. The original Python backend is not modified or included in this package.

## Start on your Mac

Use Node.js 22 or later. Check with `node --version`. Open this folder in VS Code and run:

```bash
npm install
npm run dev
```

Open **http://localhost:3000**. Vite requires port 3000; stop any other frontend using it first.

The application opens in **Preview mode**, using captured output from your actual source code. This works without starting Python. It is a fixed snapshot, not an active simulation or physical field data. The snapshot is for the reference fleet at 19:00 IST. The recorded simulation is for DT-104 on 15 July 2026 with the documented CLI defaults; its scenario is independent of the currently selected fleet transformer.

## Connect your backend

1. Keep the existing backend running at `http://localhost:8000`.
2. Open the frontend at **http://localhost:3000** (not `127.0.0.1`, unless you configure CORS for it).
3. Click **Connect backend**.
4. Leave the API base URL as `http://localhost:8000` and choose **DISCOM operator**.
5. Click **Connect with local demo login**. This uses the existing `/api/v1/auth/dev-token` endpoint. Nothing is added to your Python project.

You can now run simulations, set the twin clock, run operator cycles, inspect dispatches and use the backend Copilot. Development tokens stay in memory and are cleared on page refresh. Role selection affects UI visibility; the backend must enforce permissions. The token-entry option requires choosing the matching role. This is a development client, not a complete production OIDC login implementation.

An `.env.example` is provided. Copy it to `.env` if you need a different API URL, then restart Vite. API errors do not switch the app to preview automatically.

## Show the demo

- Overview: select a transformer from the ranked list.
- Neighbourhood twin: inspect the schematic and click a household. Move the clock in connected mode.
- Forecasts & reliability: inspect demand uncertainty and shortage windows. The Reliability Budget appears after a simulation for that transformer or a reliability cycle.
- Simulation lab: preview includes one recorded result. Connect the backend to edit inputs and run a new scenario. Scrub or play the returned timeline and export the complete result as JSON.
- Dispatch & safety: explicitly run a cycle as DISCOM operator. The backend may auto-dispatch validated actions below its approval threshold. Inspect actual states and approve or reject only when awaiting approval.
- Community & fairness: filter households, inspect burden and debt. Urja Sakhi can access the supported local emergency controls through the twin page.
- Jyoti: connected mode forwards the conversation to the backend. Offline/fallback answers are identified using its returned mode.

## Data provenance and interpretation

`public/preview-data.json` was captured by running the supplied `src 2.zip` in a fresh local SQLite test instance. It includes all eight transformer snapshots, forecasts, topology, fairness and edge state, plus the actual reference simulation. No API token is included. Snapshot values are immutable in preview. The real-time event stream is labelled separately from the persistent **Simulation data** badge.

The reference simulation reproduced 465 → 0 critical outage home-hours and 338.95 → 16.86 kWh unmet demand. These are scenario outputs, not universal reliability guarantees. The baseline has no storage or flexible-load coordination, while Jyotiveda uses configured storage and flexibility. Outage home-hours sum outage time across households. Essential-power availability is distinct from fully served demand. The clock follows the model's scenario date, not today's date.

The topology is a schematic, not a geographic map. Household squares correspond to backend topology nodes. Roof solar and shared storage are aggregate resource annotations, not individual topology edges. The default overview without topology is an illustrative aggregate resource view.

## Known backend blockers (not changed)

1. **Audit startup persistence:** `AuditLedger` starts with an empty record list. `Platform.start()` appends record 1 without restoring persisted history. Restarting against a populated SQLite database can fail with `UNIQUE constraint failed: audit_log.seq`. The durable repair must restore/verify the full audit chain before appending, serialize writes, and preserve existing records. Changing only the sequence is insufficient because the previous hash matters. Your already-running fresh demo database can be used for this frontend. Do not delete your original database.
2. **Role/scope enforcement:** route-level roles exist, but some collection and simulation routes do not enforce transformer scopes consistently. Client visibility is not a substitute for server authorization. Review before using real household data or deploying beyond a trusted demo.
3. **Fairness runtime:** the supplied operational cycle does not advance the household fairness ledger. Captured fleet fairness can therefore be zero; the simulation's independent ledger and served-energy Gini are separate. No artificial heatmap values are added.
4. **Optional model/infrastructure paths:** the CPU forecasting fallback and simulation are usable. External LLM availability depends on provider configuration and the actual configured model. Production OIDC, Kafka, MQTT and physical hardware integration were not established here.
5. **Affordability:** modelled resource costs are available, but community ownership, maintenance costs, household tariffs and payback are not established by the source files. These remain team submission inputs.
6. **Persistence beyond audits:** dispatch and market state are primarily read from process memory. Review restoration after restart before treating the UI as a durable operations console.

## API map

| Screen/action | Existing API |
|---|---|
| Login | POST `/api/v1/auth/dev-token` |
| Overview | GET `/api/v1/analytics/summary`, `/api/v1/transformers` |
| Twin | GET `/api/v1/transformers/{id}/topology`, `/api/v1/edge/{id}` |
| Clock | POST `/api/v1/transformers/{id}/clock` |
| Forecast | GET `/api/v1/forecast/{id}?kind=all` |
| Budget / operational cycle | POST `/api/v1/reliability/{id}/cycle` |
| Scenario | POST `/api/v1/simulation/run` |
| Dispatch | GET `/api/v1/dispatch?transformer_id=…`; POST `/{id}/approve` or `/{id}/reject` |
| Audit | GET `/api/v1/audit?entity_id=…`, `/api/v1/audit/verify` |
| Fairness | GET `/api/v1/fairness/{id}`, `/api/v1/fairness/{id}/households/{hh}` |
| Community emergency | POST `/api/v1/dispatch/emergency/{id}?enable=…` |
| Copilot | POST `/api/v1/copilot/chat` |
| Updates | WebSocket `/ws/live?token=…&topics=…&transformer_id=…` |

WebSocket reconnects with backoff and marks stale connections. Query refresh is throttled. On error, existing cached data may remain visible with a visible connection/error state. No model results are fabricated.

## Build

```bash
npm run build
npm run preview
```

`dist/` contains the compiled application. Serve it over HTTP; do not open `index.html` directly with Finder. `npm run preview` also uses port 3000.

## Files

The ZIP also includes the production build in `dist/` and visual checks in `qa/`. Dependency folders and Python environments are excluded.

- `src/App.tsx`: screens, workflows, permissions and backend queries.
- `src/components.tsx`: charts, topology diagram, metrics, budget and accessible drawers.
- `src/types.ts`: contracts derived from supplied Python code.
- `src/api.ts`: authenticated client, timeouts and error messages.
- `src/styles.css`: responsive visual system and reduced-motion handling.
- `public/preview-data.json`: backend-generated preview fixture.
- `VALIDATION.md`: checks and practical limits.

UI fonts load from Google Fonts with system fallbacks. No analytics or third-party AI keys are embedded. The primary deliverable is a local frontend project; no public service or live device connection has been deployed.

## Branding update

The sidebar, footer and favicon use a custom J monogram with a solar dot and connected nodes. Editable SVG versions are in `public/`. See `LOGO-UPDATE.md` for applying the update to an already-running copy.
