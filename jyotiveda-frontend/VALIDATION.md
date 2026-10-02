# Validation

Checked on 30 September 2026 with Node.js 24.19, Chromium 134 and a fresh local test instance of the supplied Python backend. The original project and database were not modified.

## Completed checks

- TypeScript strict compilation and Vite production build passed.
- All six screens opened without browser runtime errors.
- Desktop 1440 × 1050 and mobile 390 × 844 layouts checked. No document-level horizontal overflow on the tested screens. The topology has its own horizontal scroll area on small displays.
- Simulation replay, household drawer, mobile navigation and connection drawer exercised.
- Connected to the real backend with the development-token endpoint.
- Changed the twin clock and refreshed returned state.
- Ran the real reference simulation, rendered the returned results and downloaded its JSON.
- Ran an operator reliability cycle and inspected the resulting dispatch.
- Verified the backend audit chain and opened household fairness details.
- Sent a Copilot message and displayed the backend's `offline` response mode.
- Logged in as Urja Sakhi, checked restricted navigation and enabled the twin's emergency mode.
- The connected browser run recorded no failed API responses and no browser runtime errors.

The saved reference result reproduces the previously supplied critical-outage, essential-availability and unmet-energy metrics. Preview data is from the source's digital twin, not field measurements.

## Included evidence

`qa/` includes browser check results and desktop/mobile screenshots of Overview and Simulation lab. `qa.mjs` exercises the preview. `qa-connected.mjs` exercises connected workflows and sends mutations to a backend test instance.

To repeat the preview check after installing dependencies:

```bash
npx playwright install chromium
node qa.mjs
```

Run `qa-connected.mjs` only against a disposable local test instance. It uses `http://127.0.0.1:8000` and serves the frontend at `http://127.0.0.1:3000`, which must be in that test backend's CORS origins. It creates a simulation and dispatch, changes the model clock, and enables emergency mode. Port 3000 must be free for either script.

## Practical limits

- Approve/reject controls are wired to the supplied API and state contract, but an awaiting-approval case was not exercised in the browser run.
- No production identity provider, external AI model, real device, Kafka or MQTT deployment was tested.
- No Safari, Firefox or physical-phone test was run.
- The production build reports a bundle-size advisory for the chart-heavy JavaScript bundle (about 742 kB uncompressed, 212 kB gzip). This is not a build failure.
- Automated accessibility certification and load testing were not performed.
- Existing backend persistence and authorization limitations are documented in README.md.

## Logo update verification

The custom SVG logo update passed TypeScript compilation and the production build. Preview browser checks and desktop/mobile screenshots were refreshed. No backend or dependency changes were included in this visual update.
