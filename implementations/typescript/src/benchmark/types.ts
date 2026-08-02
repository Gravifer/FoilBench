import type {Scenario, SolverId} from "../core/contracts.js";

export interface BenchmarkMatrix {readonly id: string; readonly scenario: string; readonly solvers: readonly SolverId[]; readonly resolutions: readonly (readonly [number, number])[]; readonly duration: number; readonly repetitions: number; readonly saveSnapshots: boolean}
export interface BrowserRunRequest {readonly scenario: Scenario; readonly solverId: SolverId; readonly duration: number}
export interface BrowserRunResult {readonly initializationSeconds: number; readonly coldStepSeconds: number; readonly stepSeconds: readonly number[]; readonly simulatedSeconds: number; readonly substeps: number; readonly diagnostics: Readonly<Record<string, number>>; readonly warnings: readonly string[]; readonly success: boolean}
