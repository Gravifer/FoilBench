import {expect, test} from "@playwright/test";

test("the public lab runs both browser engines and retains accessible controls", async ({page}) => {
  await page.goto("./?preset=dynamic&solver=stable-fluids&backend=typescript", {waitUntil: "domcontentloaded"});
  await expect(page.getByRole("heading", {level: 1, name: "FoilBench"})).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});
  await expect(page.getByRole("button", {name: "Lab guide"})).toBeVisible();
  await expect(page.locator("canvas")).toHaveCSS("touch-action", "none");

  await page.getByRole("button", {name: "Rust / WASM", exact: true}).click();
  await expect(page).toHaveURL(/backend=rust-wasm/);
  await expect(page.getByLabel("Simulation running")).toBeVisible({timeout: 30_000});
  await expect(page.getByRole("button", {name: "Rust / WASM", exact: true})).toHaveAttribute("aria-pressed", "true");
});
