import type {FlowSolver, Scenario} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {bounds2d, dimensions} from "../core/grid.js";
import {Pcg32} from "../core/rng.js";

export type TracerMode = "display" | "material";
export type BoundaryExitTrailPolicy = "clear" | "age-out";
export type TracerRecycleReason = "boundary_exit" | "lifetime_expiry" | "invalid_collision" | "forced_recovery" | "scenario_reset" | "periodic_wrap";
type Placement = "domain" | "inlet";

export type TracerRecycleCounters = Readonly<Record<TracerRecycleReason, number>>;

const makeCounters = (): Record<TracerRecycleReason, number> => ({
  boundary_exit: 0,
  lifetime_expiry: 0,
  invalid_collision: 0,
  forced_recovery: 0,
  scenario_reset: 0,
  periodic_wrap: 0,
});

export function defaultTracerCount(scenario: Scenario): number {
  const {x, y} = bounds2d(scenario.domain);
  const chordArea = scenario.foil.chord * scenario.foil.chord;
  const selected = Math.round(256 * (x[1] - x[0]) * (y[1] - y[0]) / chordArea);
  return Math.max(2048, Math.min(8192, selected));
}

export class TracerSystem {
  public readonly positions: Float32Array;
  public readonly ages: Float32Array;
  public readonly lifetimes: Float32Array;
  public readonly count: number;
  private readonly history: Float32Array;
  private readonly generations: Uint32Array;
  private readonly historyGenerations: Uint32Array;
  private readonly midpoints: Float32Array;
  private readonly rng: Pcg32;
  private readonly foil: NacaFoil;
  private readonly counters = makeCounters();
  private currentMode: TracerMode = "display";
  private boundaryExitTrailPolicyValue: BoundaryExitTrailPolicy = "clear";
  private cursor = 0;

  public constructor(private readonly scenario: Scenario, count = defaultTracerCount(scenario), private readonly depth = 12) {
    if (!Number.isInteger(count) || count <= 0) throw new RangeError("tracer count must be a positive integer");
    if (!Number.isInteger(depth) || depth < 2) throw new RangeError("tracer history depth must be at least two");
    this.count = count;
    this.positions = new Float32Array(2 * count);
    this.ages = new Float32Array(count);
    this.lifetimes = new Float32Array(count);
    this.history = new Float32Array(2 * count * depth);
    this.generations = new Uint32Array(count);
    this.historyGenerations = new Uint32Array(count * depth);
    this.midpoints = new Float32Array(2 * count);
    this.rng = new Pcg32(scenario.seed, 97);
    this.foil = new NacaFoil(scenario.foil);
    this.reseed(scenario.controls[0]?.angleDegrees ?? 0);
  }

  public get mode(): TracerMode { return this.currentMode; }
  public get boundaryExitTrailPolicy(): BoundaryExitTrailPolicy { return this.boundaryExitTrailPolicyValue; }
  public get recycleCounters(): TracerRecycleCounters { return {...this.counters}; }
  public get maximumSegmentScalars(): number { return 4 * this.count * (this.depth - 1); }
  public get maximumSegmentCount(): number { return this.count * (this.depth - 1); }

  public setMode(mode: TracerMode): void {
    if (mode === this.currentMode) return;
    if (mode === "display") for (let index = 0; index < this.count; index += 1) this.resetLifetime(index, false);
    this.currentMode = mode;
  }

  public toggleMode(): TracerMode {
    this.setMode(this.currentMode === "display" ? "material" : "display");
    return this.currentMode;
  }

  public setBoundaryExitTrailPolicy(policy: BoundaryExitTrailPolicy): void {
    this.boundaryExitTrailPolicyValue = policy;
  }

  public reseed(angleDegrees: number, reason: "forced_recovery" | "scenario_reset" | null = null): void {
    for (let index = 0; index < this.count; index += 1) {
      this.generations[index] = (this.generations[index] ?? 0) + 1;
      this.place(index, angleDegrees, "domain");
      this.resetLifetime(index, true);
      this.resetHistory(index);
    }
    this.cursor = 0;
    if (reason !== null) this.counters[reason] += this.count;
  }

  private resetLifetime(index: number, staggerAge: boolean): void {
    const lifetime = 3 + 4 * this.rng.nextFloat32();
    this.lifetimes[index] = lifetime;
    this.ages[index] = staggerAge ? lifetime * this.rng.nextFloat32() : 0;
  }

  private validPoint(px: number, py: number, angleDegrees: number): boolean {
    const {x, y} = bounds2d(this.scenario.domain);
    return Number.isFinite(px) && Number.isFinite(py) && px >= x[0] && px <= x[1] && py >= y[0] && py <= y[1] && this.foil.signedDistance(px, py, angleDegrees) > 0;
  }

  private place(index: number, angleDegrees: number, placement: Placement): void {
    const {x, y} = bounds2d(this.scenario.domain);
    const width = x[1] - x[0]; const height = y[1] - y[0];
    for (let attempt = 0; attempt < 32; attempt += 1) {
      const px = placement === "domain" ? x[0] + this.rng.nextFloat32() * width : x[0] + 0.01 * width * this.rng.nextFloat32();
      const py = y[0] + this.rng.nextFloat32() * height;
      if (this.validPoint(px, py, angleDegrees)) { this.positions[2 * index] = px; this.positions[2 * index + 1] = py; return; }
    }
    for (let attempt = 0; attempt < 4096; attempt += 1) {
      const selected = (index * 977 + attempt) % 4096; const ix = selected % 64; const iy = Math.floor(selected / 64);
      const px = placement === "domain" ? x[0] + (ix + 0.5) * width / 64 : x[0] + 0.005 * width;
      const py = y[0] + (iy + 0.5) * height / 64;
      if (this.validPoint(px, py, angleDegrees)) { this.positions[2 * index] = px; this.positions[2 * index + 1] = py; return; }
    }
    throw new RangeError("unable to place a visible tracer outside the foil");
  }

  private respawn(index: number, angleDegrees: number, reason: "boundary_exit" | "lifetime_expiry" | "invalid_collision", placement: Placement): void {
    this.generations[index] = (this.generations[index] ?? 0) + 1;
    this.place(index, angleDegrees, placement);
    this.resetLifetime(index, false);
    if (reason !== "boundary_exit" || this.boundaryExitTrailPolicyValue === "clear") this.resetHistory(index);
    this.counters[reason] += 1;
  }

  private resetHistory(index: number): void {
    const offset = 2 * index;
    for (let frame = 0; frame < this.depth; frame += 1) {
      this.history[frame * this.positions.length + offset] = this.positions[offset] ?? 0;
      this.history[frame * this.positions.length + offset + 1] = this.positions[offset + 1] ?? 0;
      this.historyGenerations[frame * this.count + index] = this.generations[index] ?? 0;
    }
  }

  private breakContinuity(index: number, reason: "periodic_wrap"): void {
    this.generations[index] = (this.generations[index] ?? 0) + 1;
    this.resetHistory(index);
    this.counters[reason] += 1;
  }

  public advance(solver: FlowSolver, dt: number): void {
    if (!(dt > 0) || !Number.isFinite(dt)) throw new RangeError("tracer dt must be finite and positive");
    const initial = solver.sampleVelocity(this.positions);
    if (initial.length !== this.positions.length || !initial.every(Number.isFinite)) throw new RangeError("initial tracer velocity sample is invalid");
    for (let scalar = 0; scalar < this.positions.length; scalar += 1) this.midpoints[scalar] = (this.positions[scalar] ?? 0) + 0.5 * dt * (initial[scalar] ?? 0);
    const midpointVelocity = solver.sampleVelocity(this.midpoints);
    if (midpointVelocity.length !== this.positions.length || !midpointVelocity.every(Number.isFinite)) throw new RangeError("midpoint tracer velocity sample is invalid");

    const state = solver.exportState(); const angle = state.angleDegrees;
    const {x, y} = bounds2d(this.scenario.domain); const {dx, dy} = dimensions(this.scenario.domain);
    const periodicX = this.scenario.domain.periodicAxes.includes("x"); const periodicY = this.scenario.domain.periodicAxes.includes("y");
    const width = x[1] - x[0]; const height = y[1] - y[0];
    for (let index = 0; index < this.count; index += 1) {
      const offset = 2 * index;
      let px = (this.positions[offset] ?? 0) + dt * (midpointVelocity[offset] ?? 0);
      let py = (this.positions[offset + 1] ?? 0) + dt * (midpointVelocity[offset + 1] ?? 0);
      this.ages[index] = (this.ages[index] ?? 0) + dt;

      const outside = (!periodicX && (px < x[0] || px > x[1])) || (!periodicY && (py < y[0] || py > y[1]));
      if (outside) { this.respawn(index, angle, "boundary_exit", "inlet"); continue; }

      let wrapped = false;
      if (periodicX && (px < x[0] || px >= x[1])) { px = x[0] + (((px - x[0]) % width) + width) % width; wrapped = true; }
      if (periodicY && (py < y[0] || py >= y[1])) { py = y[0] + (((py - y[0]) % height) + height) % height; wrapped = true; }
      this.positions[offset] = px; this.positions[offset + 1] = py;

      const distance = this.foil.signedDistance(px, py, angle);
      if (!Number.isFinite(distance)) { this.respawn(index, angle, "invalid_collision", this.currentMode === "material" ? "inlet" : "domain"); continue; }
      if (distance < 0) {
        const normal = this.foil.normal(px, py, angle); const normalNorm = Math.hypot(normal[0], normal[1]);
        if (distance >= -0.5 * Math.min(dx, dy) && normal.every(Number.isFinite) && normalNorm > 1e-8) {
          const correction = (-distance + 1e-4) / normalNorm;
          this.positions[offset] = px + correction * normal[0]; this.positions[offset + 1] = py + correction * normal[1];
        } else { this.respawn(index, angle, "invalid_collision", this.currentMode === "material" ? "inlet" : "domain"); continue; }
      }

      if (this.currentMode === "display" && (this.ages[index] ?? 0) >= (this.lifetimes[index] ?? 0)) { this.respawn(index, angle, "lifetime_expiry", "domain"); continue; }
      if (wrapped) this.breakContinuity(index, "periodic_wrap");
    }
    this.cursor = (this.cursor + 1) % this.depth;
    this.history.set(this.positions, this.cursor * this.positions.length);
    this.historyGenerations.set(this.generations, this.cursor * this.count);
  }

  public segments(destination?: Float32Array): Float32Array {
    return this.collectSegments(destination).segments;
  }

  public segmentsWithAges(segmentDestination?: Float32Array, ageDestination?: Uint8Array): {readonly segments: Float32Array; readonly ages: Uint8Array} {
    const ageCapacity = this.count * (this.depth - 1);
    if (ageDestination !== undefined && ageDestination.length < ageCapacity) throw new RangeError("path age destination is too small");
    const result = this.collectSegments(segmentDestination, ageDestination ?? new Uint8Array(ageCapacity));
    if (result.ages === undefined) throw new Error("path age collection was not requested");
    return {segments: result.segments, ages: result.ages};
  }

  private collectSegments(segmentDestination?: Float32Array, ageDestination?: Uint8Array): {readonly segments: Float32Array; readonly ages?: Uint8Array} {
    const capacity = 4 * this.count * (this.depth - 1);
    if (segmentDestination !== undefined && segmentDestination.length < capacity) throw new RangeError("path destination is too small");
    const output = segmentDestination ?? new Float32Array(capacity);
    let outputCursor = 0;
    let segmentCursor = 0;
    for (let age = this.depth - 1; age > 0; age -= 1) {
      const older = (this.cursor - age + this.depth) % this.depth; const newer = (older + 1) % this.depth;
      for (let index = 0; index < this.count; index += 1) {
        if (this.historyGenerations[older * this.count + index] !== this.historyGenerations[newer * this.count + index]) continue;
        const offset = 2 * index;
        output[outputCursor] = this.history[older * this.positions.length + offset] ?? 0;
        output[outputCursor + 1] = this.history[older * this.positions.length + offset + 1] ?? 0;
        output[outputCursor + 2] = this.history[newer * this.positions.length + offset] ?? 0;
        output[outputCursor + 3] = this.history[newer * this.positions.length + offset + 1] ?? 0;
        if (ageDestination !== undefined) ageDestination[segmentCursor] = Math.round(255 * (this.depth - 1 - age) / (this.depth - 2));
        outputCursor += 4;
        segmentCursor += 1;
      }
    }
    return ageDestination === undefined
      ? {segments: output.subarray(0, outputCursor)}
      : {segments: output.subarray(0, outputCursor), ages: ageDestination.subarray(0, segmentCursor)};
  }
}
