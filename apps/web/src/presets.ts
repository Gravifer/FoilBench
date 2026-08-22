import chaoticUrl from "../../../scenarios/airfoil/chaotic-experimental.json?url";
import defaultUrl from "../../../scenarios/airfoil/default.json?url";
import fixedStallUrl from "../../../scenarios/airfoil/fixed-stall.json?url";
import referenceUrl from "../../../scenarios/airfoil/reference.json?url";
import schemaUrl from "../../../spec/schemas/scenario.schema.json?url";

import type {Scenario} from "foilbench-typescript/src/core/contracts.js";
import {parseScenario} from "foilbench-typescript/src/core/scenario.js";

export interface PresetDefinition {
  readonly id: string;
  readonly label: string;
  readonly summary: string;
  readonly url: string;
  readonly expensive?: boolean;
}

export const PRESETS: readonly PresetDefinition[] = [
  {id: "dynamic", label: "Dynamic stall", summary: "A guided 4° → 14° → 25° → 4° excursion.", url: defaultUrl},
  {id: "fixed-stall", label: "Fixed stall", summary: "Hold 25° and watch the separated wake develop.", url: fixedStallUrl},
  {id: "chaotic", label: "Chaotic wake", summary: "High-Re skew-RK2 transport for an irregular 2D wake.", url: chaoticUrl},
  {id: "reference", label: "High-resolution reference", summary: "A heavier 384×192 dynamic run intended for capable computers.", url: referenceUrl, expensive: true},
];

let schemaPromise: Promise<object> | null = null;

async function scenarioSchema(): Promise<object> {
  schemaPromise ??= fetch(schemaUrl).then(async (response) => {
    if (!response.ok) throw new Error(`scenario schema failed to load (${String(response.status)})`);
    return response.json() as Promise<object>;
  });
  return schemaPromise;
}

export async function parseScenarioDocument(document: unknown): Promise<Scenario> {
  return parseScenario(document, await scenarioSchema());
}

export async function loadPreset(preset: PresetDefinition): Promise<Scenario> {
  const response = await fetch(preset.url);
  if (!response.ok) throw new Error(`${preset.label} failed to load (${String(response.status)})`);
  return parseScenarioDocument(await response.json() as unknown);
}
