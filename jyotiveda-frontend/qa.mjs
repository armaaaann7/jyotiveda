import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
const vite = spawn(
  process.execPath,
  ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "3000"],
  { stdio: "inherit" },
);
const results = [];
let browser;
try {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch("http://127.0.0.1:3000")).ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1050 },
    deviceScaleFactor: 1,
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:3000");
  await page.getByText("Hadapsar Gadital", { exact: true }).waitFor();
  await page.waitForTimeout(700);
  mkdirSync("qa", { recursive: true });
  await page.screenshot({ path: "qa/overview-desktop.png", fullPage: true });
  for (const label of [
    "Neighbourhood twin",
    "Forecasts & reliability",
    "Simulation lab",
    "Dispatch & safety",
    "Community & fairness",
  ]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.waitForTimeout(250);
    results.push({
      view: label,
      heading: await page.locator("h1").textContent(),
      overflow: await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    });
    if (label === "Simulation lab") {
      await page.screenshot({
        path: "qa/simulation-desktop.png",
        fullPage: true,
      });
      await page
        .getByRole("button", { name: "Play replay", exact: true })
        .click();
      await page.waitForTimeout(600);
      await page
        .getByRole("button", { name: "Pause replay", exact: true })
        .click();
    }
    if (label === "Community & fairness") {
      await page.locator(".household").first().click();
      await page.getByRole("dialog").waitFor();
      await page.getByRole("button", { name: "Close panel" }).click();
    }
  }
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: "qa/overview-mobile.png", fullPage: true });
  results.push({
    view: "mobile",
    overflow: await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  });
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await page
    .getByRole("button", { name: "Simulation lab", exact: true })
    .click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: "qa/simulation-mobile.png", fullPage: true });
  results.push({
    view: "mobile simulation",
    overflow: await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  });
  results.push({ browserErrors: errors });
  writeFileSync("qa/preview-checks.json", JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results));
} finally {
  await browser?.close();
  vite.kill();
}
