import {expect, test} from "@playwright/test";
import {Buffer} from "node:buffer";
import {resolve} from "node:path";

test("static SPA defaults to TypeScript and retains the Rust/WASM comparison backend", async ({page}) => {
  await page.goto("./");
  await expect(page.getByRole("heading", {level: 1, name: "FoilBench"})).toBeVisible();
  await expect(page.getByText("Toy 2D wind tunnel with an airfoil", {exact: true})).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.getByText("TypeScript", {exact: true}).first()).toBeVisible();
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});

  const lbm = page.getByRole("button", {name: "D2Q9 LBM"});
  await lbm.click();
  await expect(lbm).toHaveClass(/active/, {timeout: 30_000});
  await page.getByRole("button", {name: "Stable Fluids"}).click();
  await page.getByRole("button", {name: "Rust / WASM", exact: true}).click();
  await expect(page.getByText("Rust / WASM", {exact: true}).first()).toBeVisible();
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});
  await expect(page).toHaveURL(/backend=rust-wasm/);
});

test("curated controls and local scenario import remain browser-local", async ({page}) => {
  await page.goto("./?preset=fixed-stall&solver=stable-fluids&backend=typescript");
  await expect(page.getByLabel("Experiment")).toHaveValue("fixed-stall");
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});

  await page.getByLabel("Angle of attack").fill("12");
  await page.getByLabel("Angle of attack").press("ArrowRight");
  await expect(page.getByText("manual control", {exact: false})).toBeVisible();
  await page.waitForTimeout(1200);
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
  const controls = page.getByRole("button", {name: "Show controls"});
  await expect(controls).toHaveAttribute("aria-expanded", "false");
  await controls.click();
  await expect(page.getByRole("button", {name: "Hide controls"})).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByLabel("Preset")).toBeVisible();
  await expect(page.getByLabel("Angle of attack")).toBeVisible();
  await expect(page.getByRole("button", {name: "Pause simulation"})).toBeVisible({timeout: 30_000});
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", {name: "Show controls"})).toHaveAttribute("aria-expanded", "false");
});

test("wide and narrow control-panel preferences remain independent", async ({page}) => {
  await page.setViewportSize({width: 1280, height: 720});
  await page.goto("./?backend=typescript");
  const panel = page.getByLabel("Simulation controls");
  const stage = page.getByLabel("Interactive airflow visualization");
  await expect(page.getByRole("button", {name: "Hide controls"})).toBeVisible();
  await expect(panel).toBeVisible();
  const openStage = await stage.boundingBox();
  expect(await page.locator("main").evaluate((main) => getComputedStyle(main).transitionProperty)).toContain("grid-template-columns");
  await page.getByRole("button", {name: "Hide controls"}).click();
  await expect(panel).toBeHidden();
  const closedStage = await stage.boundingBox();
  expect((closedStage?.width ?? 0) - (openStage?.width ?? 0)).toBeGreaterThan(300);

  await page.setViewportSize({width: 390, height: 720});
  await expect(page.getByRole("button", {name: "Show controls"})).toBeVisible();
  await page.getByRole("button", {name: "Show controls"}).click();
  await expect(panel).toBeVisible();

  await page.setViewportSize({width: 1280, height: 720});
  await expect(page.getByRole("button", {name: "Show controls"})).toBeVisible();
  await expect(panel).toBeHidden();
  await page.setViewportSize({width: 390, height: 720});
  await expect(page.getByRole("button", {name: "Hide controls"})).toBeVisible();
  await expect(panel).toBeVisible();
});

test("the centered transport, key hints, and solver-aware tuning remain semantic", async ({page}) => {
  await page.goto("./?backend=typescript&solver=stable-fluids");
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});

  const header = page.locator("header");
  const transport = page.getByLabel("Simulation transport");
  const pauseButton = transport.getByRole("button", {name: "Pause simulation"});
  const phaseStatus = header.locator(".header-status");
  const controlsToggle = page.getByRole("button", {name: "Hide controls"});
  await expect(transport.getByRole("button", {name: "Reset simulation"})).toHaveAttribute("title", "Reset (R)");
  await expect(transport.getByRole("button", {name: "Pause simulation"})).toHaveAttribute("title", "Pause (Space)");
  await expect(transport.locator("kbd")).toHaveCount(0);
  await expect(phaseStatus).toHaveClass(/status-running/);
  const bulbPaint = await phaseStatus.locator(".status-bulb").evaluate((bulb) => {
    const style = getComputedStyle(bulb);
    return {background: style.backgroundColor, color: style.color, shadow: style.boxShadow};
  });
  expect(bulbPaint.background).toBe(bulbPaint.color);
  expect(bulbPaint.shadow).toBe("none");
  await expect(header.locator(".playback-time")).toHaveText(/^t=\d+\.\d{2}$/i);
  const clockColor = await header.locator(".playback-time").evaluate((clock) => getComputedStyle(clock).color);
  expect(clockColor).not.toBe("rgb(88, 196, 221)");
  await expect(page.getByText("Flow time", {exact: true})).toHaveCount(0);
  const [headerBox, pauseBox, transportBox, controlsBox] = await Promise.all([header.boundingBox(), pauseButton.boundingBox(), transport.boundingBox(), controlsToggle.boundingBox()]);
  expect(headerBox).not.toBeNull();
  expect(pauseBox).not.toBeNull();
  expect(transportBox).not.toBeNull();
  expect(controlsBox).not.toBeNull();
  expect(Math.abs((pauseBox?.x ?? 0) + (pauseBox?.width ?? 0) / 2 - ((headerBox?.x ?? 0) + (headerBox?.width ?? 0) / 2))).toBeLessThan(1);
  expect(Math.abs((transportBox?.y ?? 0) - (controlsBox?.y ?? 0))).toBeLessThan(1);
  expect(Math.abs((transportBox?.height ?? 0) - (controlsBox?.height ?? 0))).toBeLessThan(1);
  await transport.getByRole("button", {name: "Pause simulation"}).click();
  await expect(page.getByLabel("Simulation paused")).toBeVisible();
  const pausedTransportBox = await transport.boundingBox();
  expect(pausedTransportBox).not.toBeNull();
  expect(Math.abs((pausedTransportBox?.x ?? 0) - (transportBox?.x ?? 0))).toBeLessThan(1);
  await transport.getByRole("button", {name: "Resume simulation"}).click();

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

test("header regions do not overlap across supported widths", async ({page}) => {
  await page.setViewportSize({width: 1280, height: 720});
  await page.goto("./?backend=typescript");
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});
  for (const width of [1280, 980, 768, 560, 390, 320]) {
    await page.setViewportSize({width, height: 720});
    const regions = await page.locator("header").evaluate((header) => {
      const box = (selector: string): {left: number; right: number; top: number; bottom: number} => {
        const bounds = header.querySelector(selector)?.getBoundingClientRect();
        if (bounds === undefined) throw new Error(`missing header region ${selector}`);
        return {left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom};
      };
      const visibleChildren = (selector: string): {left: number; right: number; top: number; bottom: number} => {
        const parent = header.querySelector(selector);
        if (parent === null) throw new Error(`missing header region ${selector}`);
        const bounds = [...parent.children].map((child) => child.getBoundingClientRect()).filter((child) => child.width > 0 && child.height > 0);
        if (bounds.length === 0) throw new Error(`empty header region ${selector}`);
        return {
          left: Math.min(...bounds.map((child) => child.left)),
          right: Math.max(...bounds.map((child) => child.right)),
          top: Math.min(...bounds.map((child) => child.top)),
          bottom: Math.max(...bounds.map((child) => child.bottom)),
        };
      };
      return {
        left: visibleChildren(".header-left"), playback: (() => {
          const transport = box(".header-transport");
          const meta = box(".playback-meta");
          return {left: Math.min(transport.left, meta.left), right: Math.max(transport.right, meta.right), top: Math.min(transport.top, meta.top), bottom: Math.max(transport.bottom, meta.bottom)};
        })(), right: visibleChildren(".header-right"),
        transport: box(".header-transport"), toggle: box(".controls-toggle"),
        overflow: header.scrollWidth - header.clientWidth,
      };
    });
    expect(regions.left.right).toBeLessThanOrEqual(regions.playback.left + 0.5);
    expect(regions.playback.right).toBeLessThanOrEqual(regions.right.left + 0.5);
    expect(Math.abs(regions.transport.top - regions.toggle.top)).toBeLessThan(1);
    expect(Math.abs(regions.transport.bottom - regions.toggle.bottom)).toBeLessThan(1);
    expect(regions.overflow).toBeLessThanOrEqual(0);
  }
});

test("invalid local scenarios fail visibly without leaving the browser", async ({page}) => {
  await page.goto("./?backend=typescript");
  await page.locator('input[type="file"]').setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from("{}", "utf8"),
  });
  await expect(page.locator(".stage-message").getByText("Scenario rejected:", {exact: false})).toBeVisible();
});
