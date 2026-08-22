import {expect, test} from "@playwright/test";
import {Buffer} from "node:buffer";
import {resolve} from "node:path";

test("static SPA defaults to TypeScript and retains the Rust/WASM comparison backend", async ({page}) => {
  await page.goto("./");
  await expect(page.getByRole("heading", {level: 1, name: "FoilBench"})).toBeVisible();
  await expect(page.getByText("Toy 2D wind tunnel with an airfoil", {exact: true})).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.getByText("TypeScript", {exact: true}).first()).toBeVisible();
  await expect(page.locator("header").getByText("running", {exact: true})).toBeVisible({timeout: 30_000});

  const lbm = page.getByRole("button", {name: "D2Q9 LBM"});
  await lbm.click();
  await expect(lbm).toHaveClass(/active/, {timeout: 30_000});
  await page.getByRole("button", {name: "Stable Fluids"}).click();
  await page.getByRole("button", {name: "Rust / WASM", exact: true}).click();
  await expect(page.getByText("Rust / WASM", {exact: true}).first()).toBeVisible();
  await expect(page.locator("header").getByText("running", {exact: true})).toBeVisible({timeout: 30_000});
  await expect(page).toHaveURL(/backend=rust-wasm/);
});

test("curated controls and local scenario import remain browser-local", async ({page}) => {
  await page.goto("./?preset=fixed-stall&solver=stable-fluids&backend=typescript");
  await expect(page.getByLabel("Experiment")).toHaveValue("fixed-stall");
  await expect(page.locator("header").getByText("running", {exact: true})).toBeVisible({timeout: 30_000});

  await page.getByLabel("Angle of attack").fill("12");
  await page.getByLabel("Angle of attack").press("ArrowRight");
  await expect(page.getByText("manual control", {exact: false})).toBeVisible();
  await page.getByRole("button", {name: "Vorticity"}).click();
  await page.getByRole("button", {name: /^Diagnostics/}).click();
  await expect(page.getByText("Kinetic energy")).toBeVisible();

  const scenarioPath = resolve(import.meta.dirname, "../../../scenarios/airfoil/default.json");
  await page.locator('input[type="file"]').setInputFiles(scenarioPath);
  await expect(page.getByLabel("Experiment")).toHaveValue("custom");
  await expect(page.getByText("A locally imported, schema-validated scenario.")).toBeVisible();
});

test("semantic design tokens replace Tailwind defaults", async ({page}) => {
  await page.goto("./?backend=typescript");
  const values = await page.locator("body").evaluate((body) => {
    const styles = getComputedStyle(body);
    return {
      background: styles.backgroundColor,
      family: styles.fontFamily,
      flow: getComputedStyle(document.documentElement).getPropertyValue("--color-flow").trim(),
    };
  });
  expect(values.background).toBe("rgb(28, 28, 28)");
  expect(values.family).toContain("CMU Sans Serif");
  expect(values.flow.toLowerCase()).toBe("#58c4dd");
  await expect.poll(async () => page.evaluate(() => document.fonts.check('12px "CMU Sans Serif"'))).toBe(true);
});

test("phone layout keeps the simulation playable behind a controls drawer", async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto("./?backend=typescript");
  await expect(page.locator("canvas")).toBeVisible();
  await page.getByRole("button", {name: "Controls"}).click();
  await expect(page.getByLabel("Preset")).toBeVisible();
  await expect(page.getByLabel("Angle of attack")).toBeVisible();
  await expect(page.getByRole("button", {name: "Pause"})).toBeVisible({timeout: 30_000});
});

test("the centered transport, key hints, and solver-aware tuning remain semantic", async ({page}) => {
  await page.goto("./?backend=typescript&solver=stable-fluids");
  await expect(page.locator("header").getByText("running", {exact: true})).toBeVisible({timeout: 30_000});

  const header = page.locator("header");
  const transport = page.getByLabel("Simulation transport");
  const [headerBox, transportBox] = await Promise.all([header.boundingBox(), transport.boundingBox()]);
  expect(headerBox).not.toBeNull();
  expect(transportBox).not.toBeNull();
  expect(Math.abs((transportBox?.x ?? 0) + (transportBox?.width ?? 0) / 2 - ((headerBox?.x ?? 0) + (headerBox?.width ?? 0) / 2))).toBeLessThan(3);

  await expect(page.getByText("MacCormack", {exact: true})).toBeVisible();
  await expect(page.getByText("adv=maccormack", {exact: false})).toHaveCount(0);
  await page.getByRole("button", {name: "Next transport"}).click();
  await expect(page.getByText("Skew RK2", {exact: true})).toBeVisible();
  await expect(page.getByText("Rust / WASM", {exact: true}).first()).toBeVisible();
  await expect(page.getByText("Execution engine", {exact: true})).toBeVisible();

  const crop = page.getByRole("button", {name: "Crop"});
  await expect(crop).not.toHaveClass(/active/);
  await page.keyboard.press("Control+C");
  await expect(crop).not.toHaveClass(/active/);
  await page.keyboard.press("c");
  await expect(crop).toHaveClass(/active/);
});

test("invalid local scenarios fail visibly without leaving the browser", async ({page}) => {
  await page.goto("./?backend=typescript");
  await page.locator('input[type="file"]').setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from("{}", "utf8"),
  });
  await expect(page.locator("footer").getByText("Scenario rejected:", {exact: false})).toBeVisible();
});
