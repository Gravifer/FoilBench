import {expect, test} from "@playwright/test";

test("viewer renders and applies interactive controls", async ({page}) => {
  await page.goto("/");
  const overlay = page.locator("#foilbench-overlay");
  const canvas = page.locator("canvas").first();

  await expect(canvas).toBeVisible();
  await expect(overlay).toContainText("stable-fluids", {timeout: 20_000});
  await expect(overlay).toContainText("vort=on");
  await expect(overlay).toContainText("adv=maccormack");
  await page.keyboard.press("]");
  await expect(overlay).toContainText("adv=skew-rk2");
  await page.keyboard.press("[");
  await expect(overlay).toContainText("adv=maccormack");

  await page.keyboard.press("v");
  await expect(overlay).toContainText("vort=off");
  await page.keyboard.press("c");
  await expect(overlay).toContainText("view=cropped");

  const bounds = await canvas.boundingBox();
  if (bounds === null) throw new Error("viewer canvas has no bounds");

  await page.keyboard.press("Space");
  await expect(overlay).toContainText("PAUSED");
  const angleBeforeClick = (await overlay.textContent())?.match(/AoA=\s*(-?\d+(?:\.\d+)?)/)?.[1];
  expect(angleBeforeClick).toBeDefined();
  await page.mouse.click(bounds.x + 0.80 * bounds.width, bounds.y + 0.15 * bounds.height);
  await page.waitForTimeout(100);
  const angleAfterClick = (await overlay.textContent())?.match(/AoA=\s*(-?\d+(?:\.\d+)?)/)?.[1];
  expect(angleAfterClick).toBe(angleBeforeClick);

  await page.mouse.move(bounds.x + 0.55 * bounds.width, bounds.y + 0.55 * bounds.height);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 0.62 * bounds.width, bounds.y + 0.40 * bounds.height, {steps: 4});
  await page.mouse.up();
  await expect(overlay).toContainText("manual control");
  await expect.poll(async () => (await overlay.textContent())?.match(/AoA=\s*(-?\d+(?:\.\d+)?)/)?.[1]).not.toBe(angleBeforeClick);

  const pausedText = await overlay.textContent();
  await page.waitForTimeout(150);
  expect(await overlay.textContent()).toBe(pausedText);
  await page.keyboard.press("Space");
  await expect(overlay).toContainText("running");
});

test("production viewer loads and switches the Rust/WASM solver repertoire", async ({page}) => {
  await page.goto("/?backend=rust-wasm&solver=stable-fluids");
  const overlay = page.locator("#foilbench-overlay");

  await expect(overlay).toContainText("stable-fluids [rust-wasm]", {timeout: 30_000});
  await expect(overlay).toContainText("running");
  await expect.poll(async () => {
    const text = await overlay.textContent();
    return Number(text?.match(/t=\s*(\d+(?:\.\d+)?)/)?.[1] ?? "0");
  }, {timeout: 30_000}).toBeGreaterThan(0);

  await page.keyboard.press("]");
  await expect(overlay).toContainText("adv=skew-rk2");
  await page.keyboard.press("2");
  await expect(overlay).toContainText("lbm-d2q9 [rust-wasm]", {timeout: 30_000});
  await expect(overlay).toContainText("running");
  await page.keyboard.press("1");
  await expect(overlay).toContainText("stable-fluids [rust-wasm]", {timeout: 30_000});
  await page.keyboard.press("3");
  await expect(overlay).toContainText("pic-flip [rust-wasm]", {timeout: 30_000});
  await page.keyboard.press("[");
  await expect(overlay).toContainText("FLIP", {timeout: 30_000});
  await page.keyboard.press("2");
  await expect(overlay).toContainText("lbm-d2q9 [rust-wasm]", {timeout: 30_000});
  await page.keyboard.press("3");
  await expect(overlay).toContainText("pic-flip [rust-wasm]", {timeout: 30_000});
  await page.keyboard.press("1");
  await expect(overlay).toContainText("stable-fluids [rust-wasm]", {timeout: 30_000});
});
