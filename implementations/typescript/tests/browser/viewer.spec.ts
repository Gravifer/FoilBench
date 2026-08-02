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
  await page.mouse.move(bounds.x + 0.55 * bounds.width, bounds.y + 0.55 * bounds.height);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 0.62 * bounds.width, bounds.y + 0.40 * bounds.height, {steps: 4});
  await page.mouse.up();
  await expect(overlay).toContainText(/AoA=\s+[5-9]/);

  await page.keyboard.press("Space");
  await page.waitForTimeout(100);
  const pausedText = await overlay.textContent();
  await page.waitForTimeout(150);
  expect(await overlay.textContent()).toBe(pausedText);
  await page.keyboard.press("Space");
  await expect(overlay).toContainText("running");
});
