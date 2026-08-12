import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import {describe, expect, it} from "vitest";
import type {SolverId} from "../../src/core/contracts.js";
import {parseScenario} from "../../src/core/scenario.js";
import {ViewerModel} from "../../src/viewer/model.js";

interface TranscriptAction {
  readonly sequence: number;
  readonly at: number;
  readonly kind: "step" | "pause" | "reset" | "set-angle" | "release-angle" | "switch" | "set-reynolds" | "toggle-diagnostics" | "shutdown";
  readonly angle_degrees?: number;
  readonly solver?: SolverId;
  readonly reynolds?: number;
  readonly expect: Readonly<Record<string, unknown>>;
}

interface Transcript {
  readonly scenario: string;
  readonly solver: SolverId;
  readonly actions: readonly TranscriptAction[];
}

describe("shared viewer transcript", () => {
  it("replays the language-neutral basic control sequence", async () => {
    const root = resolve("../..");
    const document = JSON.parse(await readFile(resolve(root, "spec/conformance/viewer-basic.json"), "utf8")) as unknown;
    const transcriptSchema = JSON.parse(await readFile(resolve(root, "spec/schemas/viewer-transcript.schema.json"), "utf8")) as object;
    expect(new Ajv2020({strict: true}).compile(transcriptSchema)(document)).toBe(true);
    const transcript = document as Transcript;
    const scenarioDocument = JSON.parse(await readFile(resolve(root, transcript.scenario), "utf8")) as unknown;
    const scenarioSchema = JSON.parse(await readFile(resolve(root, "spec/schemas/scenario.schema.json"), "utf8")) as object;
    const scenario = parseScenario(scenarioDocument, scenarioSchema);
    const model = new ViewerModel(scenario, transcript.solver);
    let stopped = false; let lastSequence = 0;

    for (const action of transcript.actions) {
      expect(action.sequence).toBeGreaterThan(lastSequence); lastSequence = action.sequence;
      const previousTime = model.time;
      if (action.kind === "step") model.step();
      else if (action.kind === "pause") model.paused = !model.paused;
      else if (action.kind === "reset") model.reset();
      else if (action.kind === "set-angle") model.setAngle(action.angle_degrees ?? 0, action.at * 1000);
      else if (action.kind === "release-angle") model.releaseAngle();
      else if (action.kind === "switch") model.switchSolver(action.solver ?? "stable-fluids");
      else if (action.kind === "set-reynolds") model.setReynolds(action.reynolds ?? scenario.reynolds);
      else if (action.kind === "toggle-diagnostics") model.toggleDiagnostics();
      else stopped = true;

      const state = model.sessionState(); const expected = action.expect;
      if (expected["phase"] !== undefined) expect(stopped ? "stopped" : state.phase).toBe(expected["phase"]);
      if (expected["motion_mode"] !== undefined) expect(state.motionMode).toBe(expected["motion_mode"]);
      if (expected["diagnostic_mode"] !== undefined) expect(state.diagnosticMode).toBe(expected["diagnostic_mode"]);
      if (expected["schedule_active"] !== undefined) expect(state.scheduleActive).toBe(expected["schedule_active"]);
      if (expected["angle_degrees"] !== undefined) expect(model.control(model.time).angleDegrees).toBeCloseTo(Number(expected["angle_degrees"]), 12);
      if (expected["time_relation"] === "advanced") expect(model.time).toBeGreaterThan(previousTime);
      else if (expected["time_relation"] === "unchanged") expect(model.time).toBe(previousTime);
      else if (expected["time_relation"] === "reset") expect(model.time).toBe(0);
    }
  });
});
