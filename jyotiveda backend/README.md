# Jyotiveda ज्योतिवेद

**The reliability operating system for every distribution transformer.**
Jyotiveda keeps electricity dependable in low-income Indian neighbourhoods when solar and wind dip.
It builds a live digital twin of each transformer neighbourhood and forecasts the *Reliability Gap* probabilistically.
It turns that gap into a fairness-aware *Reliability Budget* and optimises storage, flexible loads and peer-to-peer sharing with risk-aware MPC.
Only commands a deterministic safety shield has validated reach the grid.

> **Rule 1: AI recommends; physics and a deterministic safety layer decide.**
> Physics → Digital Twin → AI → Reliability Engine → Safe Optimisation → Control → UI.

---

## Measured result (digital twin, reference site DT-104)

250 kVA DT · 180 connections · 119 kWp rooftop solar · 200 kWh second-life LFP · monsoon cloud shock (-60% solar) and an evening supply cap of 70 kW from 17:30 to 22:30.
Same physical day, same weather, same demand. The planner only sees **forecasts**, while the physics runs on the **actual** day.

| KPI | Baseline (today's practice) | **Jyotiveda** |
|---|---:|---:|
| Critical / lifeline outage (home-hours) | 465.0 | **0.0** |
| Household outage (home-hours, SAIDI-style) | 465.0 | **0.0** |
| Lifeline availability in the scarcity window | 42.6 % | **100 %** |
| Energy not served | 339 kWh | **17 kWh** (-95%) |
| Homes with uninterrupted lifeline power | 0 / 180 | **180 / 180** |
| Fairness (Gini of served ratio, lower is fairer) | 0.068 | **0.008** |

Reproduce it with `jyotiveda simulate`. CI fails the build if the improvement drops below the PRD target of a **≥70% cut in critical outage hours**.
A harsher case (95% solar loss, a 60 kWh battery and a 25 kW cap) is also tested: Jyotiveda still does better than baseline, and lifeline loads are always the last to be curtailed.

---

## What's inside

| Plane | Component | Technology | Where |
|---|---|---|---|
| Field / IoT | Smart meters, DT meters, PCS/BMS, inverters | DLMS/COSEM (IS 15959 OBIS map), Modbus/SunSpec 103/124/802, MQTT 5 | `edge/protocols.py` |
| Edge | **Edge Energy Gateway**: signature check, *local* safety shield, store-and-forward, 3-level autonomy (cloud → cached plan → lifeline controller) | asyncio, SQLite WAL outbox, Ed25519, aiomqtt | `edge/gateway.py`, `edge/runner.py` |
| Data | CloudEvents 1.0 bus with 22 topics, partitioned by transformer | Redpanda/Kafka (idempotent, acks=all, zstd), TimescaleDB hypertables + continuous aggregates, PostGIS, Redis, S3/MinIO | `events/`, `storage/`, `migrations/` |
| AI: forecasting | Demand forecast (P10/P50/P90) | **Amazon Chronos-2** foundation model (zero-shot, covariate-aware) → **LightGBM quantile + split-conformal calibration** → seasonal-naive fallback | `forecasting/backends.py` |
| AI: solar | Physics-informed PV | pvlib Ineichen clear-sky × AI-weather cloud transmittance (WeatherNext/Aurora/Earth-2-class or Open-Meteo) | `forecasting/service.py`, `weather.py` |
| AI: gap | Reliability Gap | Monte Carlo with a Gaussian copula (demand ⟂̸ solar, ρ = −0.6) → shortage probability, expected and P90 kWh, risk windows | `forecasting/service.py` |
| AI: grid | Node-level stress and voltage risk | **GraphSAGE + GATv2** (PyTorch Geometric) trained on pandapower labels; LinDistFlow physics engine (within 1% of AC power flow) as teacher and fallback | `gridintel/` |
| Twin | AC power-flow digital twin | **pandapower** (11 kV → DT → LV laterals), counterfactual, replay and what-if | `twin/` |
| Decision | **Reliability Budget** | P90 gap → merit order: P2P solar → battery (priced for degradation) → flexibility market → auto-shift → lifeline-mode curtailment | `reliability/budget.py` |
| Decision | **Flexibility Marketplace** | Fairness-aware MILP (HiGHS), uniform-price settlement, DR price cap | `flexibility/market.py` |
| Decision | **Fairness Debt** | Decaying, income-weighted burden ledger; feeds market merit order and curtailment water-filling; Gini and Jain indices | `fairness/debt.py` |
| Decision | Battery intelligence | Wöhler DoD cycle-life + Arrhenius degradation cost, temperature/SoH derating, fail-safe envelope | `battery/model.py` |
| Optimisation | **Two-stage stochastic CVaR-MPC** | CVXPY + CLARABEL/HiGHS; tiered value of lost load; non-anticipative first stage; dual values as **shadow prices** for explanations | `optimization/mpc.py` |
| Optimisation | **Constrained RL (PPO-Lagrangian)** residual policy | Gymnasium env on the twin, SB3 PPO, ONNX runtime; *health-gated*, so it is bypassed automatically if the shield keeps correcting it | `rl/`, `ml/train_ppo.py` |
| Safety | **Safety Shield** (S1–S10) | Pure-Python deterministic rules; **property-tested with Hypothesis** (400 random cases per run) | `safety/shield.py` |
| Control | **Dispatch orchestrator** | Explicit state machine (PROPOSED → … → VERIFIED), human approval above 60 kW, idempotency keys, Ed25519-signed commands with nonce and expiry, telemetry verification | `dispatch/orchestrator.py` |
| Trust | **Tamper-evident audit** | SHA-256 hash chain, append-only DB trigger, `/audit/verify` | `audit/ledger.py` |
| Agents | **Jyoti Copilot** | Claude (`claude-opus-5-5`) tool-use agent with 6 live tools and one *guarded* write; multilingual; offline intent-router fallback | `copilot/agent.py` |
| Explainability | "Why did the AI do this?" | Decision drivers, MPC shadow prices and twin counterfactuals | `explain/attribution.py` |
| Security | Zero trust | OIDC/JWKS (Keycloak), RBAC with 8 roles plus ABAC transformer scope, mTLS/signed commands, DPDP-aligned consent fields, row-level security | `security/`, `migrations/` |
| MLOps | Registry, backtest, promotion gate | MLflow, nightly CronJob backtest; the gate requires CRPS −2% **and** 70–90% calibrated coverage | `ml_backtest.py`, `forecasting/metrics.py` |
| Observability | Metrics, traces, logs, alerts | Prometheus (domain metrics such as `critical_protected_ratio` and `shield_violations_total`), OpenTelemetry, structlog JSON, Loki, 8 alert rules | `observability.py`, `deploy/observability/` |
| Deploy | Cell-based K8s, IaC, supply chain | Docker (non-root, read-only rootfs, tini), Compose full stack, Helm (PDB, NetworkPolicy, ExternalSecrets, ServiceMonitor), Terraform (AWS Mumbai: EKS, MSK, KMS), GitHub Actions (Trivy, SBOM, provenance, cosign) | `Dockerfile`, `deploy/`, `infra/`, `.github/` |

---

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"          # add ,ml for Chronos-2 / GNN / PPO training
jyotiveda simulate                              # counterfactual KPIs in ~2 s (8 s with AC power flow)
jyotiveda serve                                 # API on :8000, docs at /docs
TOKEN=$(jyotiveda token --role DISCOM_OPERATOR)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/transformers        # stress radar
curl -XPOST -H "Authorization: Bearer $TOKEN" "localhost:8000/api/v1/simulation/scenario/renewable-shock?solar_reduction=0.7"
pytest                                          # 26 tests, ~18 s
```

Full stack (Timescale, Redpanda, EMQX, Redis, Prometheus, Grafana, Loki, OTel; add `--profile ai` for MLflow + MinIO and `--profile auth` for Keycloak):

```bash
jyotiveda keygen && docker compose up -d
```

## API for the frontend team

The full contract is at `/docs` (OpenAPI). `jyotiveda schemas` exports `schemas/openapi.json` and the CloudEvent schema.

| Screen | Endpoint |
|---|---|
| Command-centre header | `GET /api/v1/analytics/summary` |
| Transformer map / stress radar | `GET /api/v1/transformers` · `GET /api/v1/transformers/{id}/topology` |
| 3D twin live flows | `WS /ws/live?token=…&topics=transformer.state,dispatch.executed` |
| "Cloud Attack" slider | `POST /api/v1/simulation/scenario/renewable-shock?solar_reduction=0.6`, which returns KPIs, a 96-slot `timeline`, `events`, `reliability_budget` and `explanation` |
| Reliability Budget bars | `reliability_budget.allocation[]` (from the cycle or the simulation) |
| Fairness heatmap and house drill-down | `GET /api/v1/fairness/{id}` · `GET /api/v1/fairness/{id}/households/{hh}` |
| Why did the AI do this? | `explanation` (simulation) · `dispatch.explanation` (cycle) |
| Timeline scrubber | `events[]` (NORMAL → RISK → BUDGET → FLEX → BATTERY → LIFELINE → PROTECTED → RECOVERY) |
| Operator approvals | `GET /api/v1/dispatch` · `POST /api/v1/dispatch/{id}/approve` |
| Urja Sakhi emergency button | `POST /api/v1/dispatch/emergency/{id}` |
| Copilot | `POST /api/v1/copilot/chat` |
| Forecast fan charts | `GET /api/v1/forecast/{id}?kind=all` |
| Chaos demo (cloud link cut) | `POST /api/v1/edge/{id}/simulate-outage` |

Demo tip: `POST /api/v1/transformers/DT-104/clock {"hour": 19}` jumps the twin to the evening peak.

## Honest status

- **Runs and is tested here:** everything in `src/` on CPU. The forecasting fallbacks, physics risk engine, MPC, shield, orchestrator, edge autonomy, API and WebSocket are all covered by the 26 tests.
- **Needs the `ml` extra and GPUs or weights:** Chronos-2 inference, GNN training/inference and PPO training. That code is complete, and the platform falls back automatically when it is absent (metered by `jyotiveda_fallback_activations_total`).
- **Not yet executed:** Kafka, Postgres/Timescale, MQTT, OIDC and the Docker build have not been run in this sandbox, because the container registries were blocked. These paths are implemented and configured, and CI builds and validates them.
- **Scaling model:** each cell is a single writer for its shard of transformers (`JYOTIVEDA_FLEET_SHARD`). You scale out by adding cells. If a cell restarts, the edge gateways keep lifeline protection running on their own.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the architecture, failure modes and control hierarchy.
