import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {describe, expect, it} from "vitest";

const spaRoot = resolve(import.meta.dirname, "../src");
const theme = readFileSync(resolve(spaRoot, "theme.css"), "utf8");
const application = readFileSync(resolve(spaRoot, "App.svelte"), "utf8");

describe("FoilBench visual language", () => {
  it("defines the semantic 3B1B-inspired palette and resets Tailwind defaults", () => {
    expect(theme).toContain("--color-*: initial");
    expect(theme).toContain("--font-*: initial");
    for (const color of ["#1c1c1c", "#58c4dd", "#83c167", "#ffff00", "#f0ac5f", "#fc6255", "#ff862f"]) expect(theme.toLowerCase()).toContain(color);
  });

  it("reserves the selected Latin and future CJK font stacks", () => {
    for (const family of ["CMU Serif", "CMU Sans Serif", "CMU Typewriter Text", "Noto Serif CJK SC", "Noto Sans CJK SC", "Sarasa Mono SC"]) expect(theme).toContain(family);
  });

  it("does not use Tailwind's generic blue-purple utility families", () => {
    const forbidden = /(?:bg|text|border|outline|ring|from|via|to)-(?:blue|indigo|violet|purple)(?:-|\b)/i;
    expect(`${theme}\n${application}`).not.toMatch(forbidden);
  });
});
