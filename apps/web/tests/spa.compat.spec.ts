import {expect, test} from "@playwright/test";

test("the public lab runs both browser engines and retains accessible controls", async ({page}) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") browserErrors.push(message.text()); });

  await page.goto("./?preset=dynamic&solver=stable-fluids&backend=typescript", {waitUntil: "domcontentloaded"});
  await expect(page.getByRole("heading", {level: 1, name: "FoilBench"})).toBeVisible();
  await expect(page.getByLabel("Simulation running")).toBeVisible();
  const canvas = page.locator("canvas");
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute("data-rendered-backend", "typescript");
  await expect(canvas).toHaveAttribute("data-rendered-revision", /^\d+$/);
  await expect(page.getByRole("button", {name: "Lab guide"})).toBeVisible();
  await expect(canvas).toHaveCSS("touch-action", "none");

  await page.getByRole("button", {name: "Rust / WASM", exact: true}).click();
  await expect(page).toHaveURL(/backend=rust-wasm/);
  await expect(page.getByLabel("Simulation running")).toBeVisible();
  await expect(canvas).toHaveAttribute("data-rendered-backend", "rust-wasm");
  await expect(canvas).toHaveAttribute("data-rendered-revision", /^\d+$/);
  await expect(page.getByRole("button", {name: "Rust / WASM", exact: true})).toHaveAttribute("aria-pressed", "true");
  expect(browserErrors).toEqual([]);
});
