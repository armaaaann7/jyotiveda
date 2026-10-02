# Jyotiveda: live demo runbook

Target: about 5 minutes, DISCOM operator role, transformer **DT-104 · Hadapsar Gadital**.
All numbers come from the running backend's digital twin. Call them **simulation results**.

## 0. Before the audience arrives

```bash
# Terminal 1: backend (fresh state every time)
cd "jyotiveda backend"
source .venv/bin/activate
make demo          # = fresh ./.demo/demo.db + jyotiveda serve --port 8000
```

```bash
# Terminal 2: frontend
cd jyotiveda-frontend
npm run dev        # http://localhost:3000
```

Check: `curl http://localhost:8000/healthz` → `{"status":"ok"}`.
Present on 1920×1080 if possible (everything up to the control loop fits above the fold).
On 1366×768 the shock control and approval box stay on screen; scroll for the lower panels.

## 1. Connect and log in

1. Open **http://localhost:3000**. The banner says *Saved backend snapshot*. Click **Connect backend**.
2. API base URL `http://localhost:8000`, role **DISCOM operator**, then **Connect with local demo login**.
3. Status strip should read BACKEND · CONNECTED, STREAM · CONNECTED, FORECAST · ACTIVE, GRID RISK · PHYSICS,
   MPC · CLARABEL, EDGE · CLOUD, AUDIT · VERIFIED. RL shows "—" until the first cycle, then NOT LOADED.

## 2. Observe the live grid

Telemetry tiles (loading, Vmin, solar, demand, battery, risk, reliability, edge) and the power-flow diagram.
Flow thickness and speed scale with real kW; the battery line reverses when charging.
In the evening window, "UPSTREAM · IMPORT NEED" can exceed the 70 kW supply cap: that excess *is* the reliability
gap Jyotiveda must close (the live view shows need before curtailment).

## 3. Inject the renewable shock

Leave **Solar reduction** at 60 % and click **INJECT RENEWABLE SHOCK** (confirm the dialog).

- Steps fill in as the backend answers: Solar drop → Risk rise → Reliability gap → Resource allocation →
  Optimisation (CVaR-MPC). The scenario takes about 8 s and the counter shows elapsed seconds.
- The power-flow diagram switches to **Scenario replay** and plays the simulated afternoon and evening: solar
  collapses, the battery takes over, flexible loads defer, and lifeline mode engages. The homes panel shows
  "Baseline (no Jyotiveda): N dark" against 0 dark.
- Then the **live** reliability cycle runs: Safety check → Dispatch.

## 4. Control loop

Six stages turn COMPLETE: Observe, Predict, Understand, Allocate, Orchestrate, Share & learn.
Expand **Engineering view** for the 11-step technical chain and the raw cycle JSON.

## 5. Reliability Budget

Required kWh, then proportional bars for P2P solar, battery, flexibility market, automatic load shift and lifeline
curtailment, plus the uncovered gap. Toggle **Scenario / Live cycle**. Message: finite resources are allocated in
merit order, and shedding is the last resort.

## 6. Safety Shield

AI proposal → S1–S10 checks (✓ / ⚠ / ✕) → authorised dispatch. Message: the optimiser never touches a device.

## 7. Approve (if required) and watch VERIFIED

If the dispatch exceeds the auto-approval policy (|P| > 60 kW or wide curtailment), the amber **Human-in-the-loop
approval** box appears with the backend's reason. Click **Approve dispatch**. The dispatch timeline moves
Authorised → Sent → Acked → Executed → **Verified**, showing the command ID, Ed25519 key ID and measured vs
requested kW. Small dispatches are auto-authorised by policy and verify without this step.

## 8. Outcome: baseline vs Jyotiveda

Panel labelled **SIMULATION RESULT**: critical outage, energy not served, lifeline availability, DT loading,
minimum voltage, flexibility shifted, P2P solar, battery use, served-energy Gini.
For the default scenario the backend has produced critical outage 465 → 0 home-hours and energy not served
≈339 → ≈20 kWh. Quote what the screen shows, not these numbers.

## 9. Fairness

Debt Gini and Jain gauges (live ledger), served-energy Gini/Jain baseline → Jyotiveda (simulation), burden
histogram, most-burdened households and top flexibility contributors.

## 10. Optional: edge resilience

**Neighbourhood twin** page → **Simulate cloud-link loss**. The edge switches to *Autonomous cached plan* and
applies its local shield. This changes twin state, so do it last.

## 11. Optional: honest model runtime

"Model runtime" panel: Physics/LinDistFlow risk; GNN architecture ready, weights not loaded; CVaR-MPC/CLARABEL;
RL fallback, not loaded. Copilot (*Ask Jyoti*) answers in `offline` mode unless an Anthropic key is configured.

## 12. Reset before the next run

Stop the backend (Ctrl-C) and run `make demo` again. The twin clock, edge mode, dispatch queue, audit chain and
fairness ledger are in memory and reset on restart; `make demo` also starts a fresh SQLite file, so simulation
history and analytics start clean. Reload the browser and reconnect.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Jyotiveda backend is temporarily unavailable…" | Backend not running, wrong URL, or the page origin is not `localhost:3000` / `127.0.0.1:3000` (CORS). |
| STREAM shows Reconnecting | Backend restarted; the client reconnects automatically. Reconnect via Connection settings if the session expired. |
| Edge shows Emergency / Cached plan at start | State from an earlier run; restart with `make demo`. |
| Port 3000 or 8000 busy | Stop the other process (Vite uses `strictPort`). |
