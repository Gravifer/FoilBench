import type {FlowSolver, Scenario} from "../core/contracts.js";
import {NacaFoil} from "../core/geometry.js";
import {bounds2d, dimensions} from "../core/grid.js";
import {Pcg32} from "../core/rng.js";

export class TracerSystem {
  public readonly positions: Float32Array;
  public mode: "display" | "flow" = "display";
  private readonly history: Float32Array;
  private readonly generations: Uint32Array;
  private readonly historyGenerations: Uint32Array;
  private readonly rng: Pcg32;
  private readonly foil: NacaFoil;
  private cursor = 0;

  public constructor(private readonly scenario: Scenario, public readonly count = 8192, private readonly depth = 12) {
    this.positions = new Float32Array(2 * count);
    this.history = new Float32Array(2 * count * depth);
    this.generations = new Uint32Array(count);
    this.historyGenerations = new Uint32Array(count * depth);
    this.rng = new Pcg32(scenario.seed, 97);
    this.foil = new NacaFoil(scenario.foil);
    this.reseed();
  }

  public get maximumSegmentScalars(): number { return 4 * this.count * (this.depth - 1); }

  public reseed(): void {
    for (let index = 0; index < this.count; index += 1) {
      this.generations[index] = (this.generations[index] ?? 0) + 1;
      this.place(index, 0, true);
      this.resetHistory(index);
    }
    this.cursor = 0;
  }

  private place(index: number, angleDegrees: number, fullDomain: boolean): void {
    const {x, y} = bounds2d(this.scenario.domain);
    for (let attempt = 0; attempt < 24; attempt += 1) {
      const px = fullDomain ? x[0] + this.rng.nextFloat32() * (x[1] - x[0]) : x[0] + 0.01 * (x[1] - x[0]) * this.rng.nextFloat32();
      const py = y[0] + this.rng.nextFloat32() * (y[1] - y[0]);
      if (this.foil.signedDistance(px, py, angleDegrees) > 0) {
        this.positions[2 * index] = px; this.positions[2 * index + 1] = py; return;
      }
    }
    this.positions[2 * index] = x[0]; this.positions[2 * index + 1] = y[0];
  }

  private respawn(index: number, angleDegrees: number): void {
    this.generations[index] = (this.generations[index] ?? 0) + 1;
    this.place(index, angleDegrees, this.mode === "display");
    this.resetHistory(index);
  }

  private resetHistory(index: number): void {
    const offset = 2 * index;
    for (let frame = 0; frame < this.depth; frame += 1) {
      this.history[frame * this.positions.length + offset] = this.positions[offset] ?? 0;
      this.history[frame * this.positions.length + offset + 1] = this.positions[offset + 1] ?? 0;
      this.historyGenerations[frame * this.count + index] = this.generations[index] ?? 0;
    }
  }

  private breakContinuity(index: number): void {
    this.generations[index] = (this.generations[index] ?? 0) + 1;
    this.resetHistory(index);
  }

  public advance(solver: FlowSolver, dt: number, displayScale: number): void {
    const sampled = solver.sampleVelocity(this.positions);
    const state = solver.exportState();
    const angle = state.angleDegrees;
    const {x, y} = bounds2d(this.scenario.domain);
    const {dx, dy} = dimensions(this.scenario.domain);
    const periodicX = this.scenario.domain.periodicAxes.includes("x");
    const periodicY = this.scenario.domain.periodicAxes.includes("y");
    for (let index = 0; index < this.count; index += 1) {
      const offset = 2 * index; const scale = this.mode === "display" ? displayScale : 1;
      let px = (this.positions[offset] ?? 0) + dt * scale * (sampled[offset] ?? 0);
      let py = (this.positions[offset + 1] ?? 0) + dt * scale * (sampled[offset + 1] ?? 0);
      let wrapped = false;
      if (periodicX && (px < x[0] || px >= x[1])) { px = x[0] + ((px - x[0]) % (x[1] - x[0]) + (x[1] - x[0])) % (x[1] - x[0]); wrapped = true; }
      if (periodicY && (py < y[0] || py >= y[1])) { py = y[0] + ((py - y[0]) % (y[1] - y[0]) + (y[1] - y[0])) % (y[1] - y[0]); wrapped = true; }
      this.positions[offset] = px; this.positions[offset + 1] = py;
      if ((!periodicX && (px < x[0] || px > x[1])) || (!periodicY && (py < y[0] || py > y[1]))) { this.respawn(index, angle); continue; }
      if (wrapped) this.breakContinuity(index);
      const distance = this.foil.signedDistance(this.positions[offset] ?? 0, this.positions[offset + 1] ?? 0, angle);
      if (distance < 0) {
        const normal = this.foil.normal(this.positions[offset] ?? 0, this.positions[offset + 1] ?? 0, angle);
        if (distance >= -1.5 * Math.min(dx, dy) && normal.every(Number.isFinite) && Math.hypot(normal[0], normal[1]) > 0.5) {
          const correction = -distance + 1e-3 * Math.min(dx, dy);
          this.positions[offset] = (this.positions[offset] ?? 0) + correction * normal[0];
          this.positions[offset + 1] = (this.positions[offset + 1] ?? 0) + correction * normal[1];
        } else this.respawn(index, angle);
      }
    }
    this.cursor = (this.cursor + 1) % this.depth;
    this.history.set(this.positions, this.cursor * this.positions.length);
    this.historyGenerations.set(this.generations, this.cursor * this.count);
  }

  public segments(destination?: Float32Array): Float32Array {
    const capacity = 4 * this.count * (this.depth - 1);
    if (destination !== undefined && destination.length < capacity) throw new RangeError("path destination is too small");
    const output = destination ?? new Float32Array(capacity); let cursor = 0;
    for (let age = this.depth - 1; age > 0; age -= 1) {
      const older = (this.cursor - age + this.depth) % this.depth; const newer = (older + 1) % this.depth;
      for (let index = 0; index < this.count; index += 1) {
        if (this.historyGenerations[older * this.count + index] !== this.historyGenerations[newer * this.count + index]) continue;
        const offset = 2 * index;
        output[cursor] = this.history[older * this.positions.length + offset] ?? 0;
        output[cursor + 1] = this.history[older * this.positions.length + offset + 1] ?? 0;
        output[cursor + 2] = this.history[newer * this.positions.length + offset] ?? 0;
        output[cursor + 3] = this.history[newer * this.positions.length + offset + 1] ?? 0;
        cursor += 4;
      }
    }
    return output.subarray(0, cursor);
  }
}
