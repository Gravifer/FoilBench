import {expect, test} from "@playwright/test";
import type {Locator, Page} from "@playwright/test";

async function expectRunningFrame(
  page: Page,
  canvas: Locator,
  backend: "typescript" | "rust-wasm",
  browserErrors: readonly string[],
): Promise<void> {
  try {
    await expect(page.getByLabel("Simulation running")).toBeVisible();
    await expect(canvas).toBeVisible();
    await expect(canvas).toHaveAttribute("data-rendered-backend", backend);
    await expect(canvas).toHaveAttribute("data-rendered-revision", /^\d+$/);
  } catch (reason) {
    const visibleState = await page.evaluate(() => ({
      phase: document.querySelector(".header-status")?.getAttribute("aria-label") ?? "missing",
      stageMessage: document.querySelector(".stage-message")?.textContent.trim() ?? null,
      statusNotice: document.querySelector(".stage-status")?.textContent.trim() ?? null,
    }));
    const cause = reason instanceof Error ? reason.message : String(reason);
    throw new Error(`${backend} did not publish a running frame: ${cause}\nVisible state: ${JSON.stringify(visibleState)}\nBrowser errors: ${JSON.stringify(browserErrors)}`, {cause: reason});
  }
}

test("the public lab runs both browser engines and retains accessible controls", async ({page}) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") browserErrors.push(message.text()); });

  await page.goto("./?preset=dynamic&solver=stable-fluids&backend=typescript", {waitUntil: "domcontentloaded"});
  await expect(page.getByRole("heading", {level: 1, name: "FoilBench"})).toBeVisible();
  const canvas = page.locator("canvas");
  await expectRunningFrame(page, canvas, "typescript", browserErrors);
  await expect(page.getByRole("button", {name: "Lab guide"})).toBeVisible();
  await expect(canvas).toHaveCSS("touch-action", "none");

  await page.getByRole("button", {name: "Rust / WASM", exact: true}).click();
  await expect(page).toHaveURL(/backend=rust-wasm/);
  await expectRunningFrame(page, canvas, "rust-wasm", browserErrors);
  await expect(page.getByRole("button", {name: "Rust / WASM", exact: true})).toHaveAttribute("aria-pressed", "true");
  expect(browserErrors).toEqual([]);
});
