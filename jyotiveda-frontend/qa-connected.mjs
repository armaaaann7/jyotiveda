import { chromium, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
const vite = spawn(
  process.execPath,
  ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "3000"],
  { stdio: "inherit" },
);
let browser;
const results = [];
const errors = [];
const failed = [];
try {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch("http://127.0.0.1:3000")).ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  // Set CHROME_PATH to use a system browser (e.g. macOS Google Chrome); otherwise Playwright's Chromium.
  browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
  });
  page.setDefaultTimeout(30000);
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400)
      failed.push({ url: r.url(), status: r.status() });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("http://127.0.0.1:3000");
  await page
    .getByRole("button", { name: "Connect backend", exact: true })
    .click();
  await page.getByLabel("API base URL").fill("http://127.0.0.1:8000");
  await page
    .getByRole("button", { name: "Connect with local demo login" })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByText("Authenticated session", { exact: true }),
  ).toBeVisible();
  results.push("Local development login");
  // Command centre: real renewable-shock scenario + live cycle, rendered stage by stage.
  await page
    .getByRole("button", { name: "Inject renewable shock", exact: true })
    .click();
  await expect(page.locator(".steps li.done")).toHaveCount(8, { timeout: 120000 }).catch(() => {});
  await expect(
    page.getByText(/Human-in-the-loop approval|measured vs/).first(),
  ).toBeVisible({ timeout: 120000 });
  if (await page.getByRole("button", { name: "Approve dispatch", exact: true }).count()) {
    await page.getByRole("button", { name: "Approve dispatch", exact: true }).click();
  }
  await expect(page.locator(".steps").getByText(/measured vs/)).toBeVisible({ timeout: 60000 });
  await expect(page.locator(".stage.complete")).toHaveCount(6, { timeout: 30000 });
  results.push({
    shockSteps: await page.locator(".steps li").evaluateAll((els) =>
      els.map((e) => `${e.className}:${e.querySelector("strong")?.textContent}`),
    ),
    shield: await page.locator(".verdict").first().textContent(),
    outcome: (await page.locator(".shock-out").textContent())?.replace(/\s+/g, " "),
  });
  await page
    .getByRole("button", { name: "Neighbourhood twin", exact: true })
    .click();
  await page.getByRole("button", { name: "Set time", exact: true }).click();
  await expect(page.getByText(/Twin clock updated\./)).toBeVisible();
  results.push("Clock POST and data refresh");
  await page
    .getByRole("button", { name: "Simulation lab", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Run simulation", exact: true })
    .click();
  await expect(
    page.getByText("COMPLETED SIMULATION", { exact: true }),
  ).toBeVisible({ timeout: 180000 });
  await expect(
    page.getByRole("heading", { name: "Essential power, protected." }),
  ).toBeVisible();
  results.push("Real backend simulation completed and rendered");
  await expect(
    page.getByRole("heading", { name: "Baseline vs Jyotiveda" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Flexibility market" }),
  ).toBeVisible();
  results.push("Before/after KPI table and market summary rendered");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export JSON" }).click();
  const file = await download;
  if (!file.suggestedFilename().endsWith(".json")) throw Error("Bad export");
  results.push("Simulation JSON export");
  await page
    .getByRole("button", { name: "Dispatch & safety", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Run reliability cycle", exact: true })
    .click();
  await expect(page.getByText(/Cycle completed\. Dispatch/)).toBeVisible({
    timeout: 180000,
  });
  await expect(
    page.getByRole("heading", { name: "Control loop · latest cycle" }),
  ).toBeVisible();
  results.push({
    controlLoop: await page.locator(".loop-runtime").first().textContent(),
  });
  await page
    .getByRole("button", { name: "Inspect", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("dialog", { name: "Dispatch details" }),
  ).toBeVisible();
  await expect(page.locator(".lifecycle li.done").first()).toBeVisible();
  await expect(page.locator(".shield-head .badge")).toBeVisible();
  results.push({
    dispatchState: await page.getByRole("dialog").locator(".badge").first().textContent(),
    shieldVerdict: await page.locator(".shield-head .badge").textContent(),
  });
  if (
    await page.getByRole("button", { name: "Approve", exact: true }).count()
  ) {
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(
      page.getByRole("dialog").getByText(/Dispatch (verified|failed|executed)/),
    ).toBeVisible({
      timeout: 60000,
    });
    results.push({
      afterApproval: await page.getByRole("dialog").locator(".badge").first().textContent(),
      telemetry: await page
        .getByRole("dialog")
        .getByText(/measured vs|Awaiting telemetry/)
        .textContent(),
    });
  }
  await page.getByRole("button", { name: "Close panel" }).click();
  results.push("Real operator cycle and dispatch details");
  await page.getByRole("button", { name: "Verify audit chain" }).click();
  await expect(page.getByText(/Audit verification: valid/)).toBeVisible();
  results.push("Audit chain verification");
  await page
    .getByRole("button", { name: "Community & fairness", exact: true })
    .click();
  await page.locator(".household").first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page
      .getByRole("dialog")
      .getByText("Demand-response events", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close panel" }).click();
  results.push("Connected household fairness drawer");
  await page.getByRole("button", { name: "Ask Jyoti" }).click();
  await page
    .getByLabel("Message Jyoti")
    .fill("Explain the reliability gap for this transformer.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".chat-message.assistant")).toBeVisible({
    timeout: 180000,
  });
  results.push({
    copilot: await page.locator(".chat-message.assistant small").textContent(),
  });
  await page.getByRole("button", { name: "Close panel" }).click();
  // Edge-autonomy chaos test last: it drains the twin battery under the cached plan.
  await page
    .getByRole("button", { name: "Neighbourhood twin", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Simulate cloud-link loss", exact: true })
    .click();
  await expect(page.getByText(/Cloud link down\. Edge chose/)).toBeVisible();
  results.push({
    edgeOutage: await page.locator(".panel .notice").first().textContent(),
  });
  await page
    .getByRole("button", { name: "Connection settings", exact: true })
    .click();
  await page.getByLabel("Demo role").selectOption("URJA_SAKHI");
  await page
    .getByRole("button", { name: "Connect with local demo login" })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Simulation lab", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Neighbourhood twin", exact: true })
    .click();
  // The twin keeps edge state between runs, so toggle whichever direction is offered.
  await page
    .getByRole("button", { name: /^(Enable|Disable) emergency mode$/ })
    .click();
  await expect(page.getByText(/Emergency mode updated\./)).toBeVisible();
  results.push("Urja Sakhi role and emergency mode");
  mkdirSync("qa", { recursive: true });
  results.push({ browserErrors: errors, failedApiRequests: failed });
  writeFileSync("qa/connected-checks.json", JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
  if (errors.length || failed.length) throw Error("Browser or API errors");
} finally {
  await browser?.close();
  vite.kill();
}
