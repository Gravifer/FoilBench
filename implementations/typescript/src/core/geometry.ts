import type {FoilSpec} from "./contracts.js";

export class NacaFoil {
  private readonly pivotX: number;
  private readonly pivotY: number;
  private readonly camber: number;
  private readonly camberPosition: number;
  private readonly thickness: number;
  private cachedAngleDegrees = Number.NaN;
  private cachedCosine = 1;
  private cachedSine = 0;
  private surfaceUpper = 0;
  private surfaceLower = 0;

  public constructor(public readonly spec: FoilSpec) {
    if (!/^\d{4}$/.test(spec.naca)) throw new TypeError("NACA code must have four digits");
    const pivotX = spec.pivot[0]; const pivotY = spec.pivot[1];
    if (pivotX === undefined || pivotY === undefined) throw new RangeError("2D foil pivot is required");
    this.pivotX = pivotX; this.pivotY = pivotY; this.camber = Number(spec.naca[0]) / 100; this.camberPosition = Number(spec.naca[1]) / 10; this.thickness = Number(spec.naca.slice(2)) / 100;
  }

  public get maximumRadius(): number { return Math.hypot(0.75 * this.spec.chord, (this.camber + 0.51 * this.thickness) * this.spec.chord); }

  private updateSurfaces(x: number): void {
    const selected = Math.max(0, Math.min(1, x / this.spec.chord));
    const selected2 = selected * selected; const selected3 = selected2 * selected; const selected4 = selected2 * selected2;
    const yt = 5 * this.thickness * this.spec.chord * (0.2969 * Math.sqrt(selected) - 0.126 * selected - 0.3516 * selected2 + 0.2843 * selected3 - 0.1036 * selected4);
    let yc = 0;
    if (this.camber > 0 && this.camberPosition > 0 && selected < this.camberPosition) yc = this.camber / (this.camberPosition * this.camberPosition) * (2 * this.camberPosition * selected - selected2);
    else if (this.camber > 0 && this.camberPosition < 1) yc = this.camber / ((1 - this.camberPosition) * (1 - this.camberPosition)) * ((1 - 2 * this.camberPosition) + 2 * this.camberPosition * selected - selected2);
    this.surfaceUpper = yc * this.spec.chord + yt; this.surfaceLower = yc * this.spec.chord - yt;
  }

  private updateRotation(angleDegrees: number): void {
    if (angleDegrees !== this.cachedAngleDegrees) { const angle = angleDegrees * Math.PI / 180; this.cachedAngleDegrees = angleDegrees; this.cachedCosine = Math.cos(angle); this.cachedSine = Math.sin(angle); }
  }

  public signedDistance(x: number, y: number, angleDegrees: number): number {
    this.updateRotation(angleDegrees); const cosine = this.cachedCosine; const sine = this.cachedSine;
    const dx = x - this.pivotX; const dy = y - this.pivotY;
    const localX = cosine * dx + sine * dy + 0.25 * this.spec.chord;
    const localY = -sine * dx + cosine * dy;
    this.updateSurfaces(localX); const upper = this.surfaceUpper; const lower = this.surfaceLower;
    const insideX = localX >= 0 && localX <= this.spec.chord;
    const verticalOutside = Math.max(localY - upper, lower - localY);
    const verticalInside = -Math.min(upper - localY, localY - lower);
    const vertical = localY <= upper && localY >= lower ? verticalInside : verticalOutside;
    const outsideX = Math.max(Math.max(-localX, localX - this.spec.chord), 0);
    return insideX ? vertical : Math.hypot(outsideX, Math.max(vertical, 0));
  }

  public normal(x: number, y: number, angleDegrees: number): readonly [number, number] {
    const epsilon = Math.max(this.spec.chord * 1e-4, 1e-6);
    let dx = this.signedDistance(x + epsilon, y, angleDegrees) - this.signedDistance(x - epsilon, y, angleDegrees);
    let dy = this.signedDistance(x, y + epsilon, angleDegrees) - this.signedDistance(x, y - epsilon, angleDegrees);
    let length = Math.hypot(dx, dy);
    if (length < epsilon) {
      this.updateRotation(angleDegrees); dx = -this.cachedSine; dy = this.cachedCosine; length = 1;
    }
    return [dx / Math.max(length, epsilon), dy / Math.max(length, epsilon)];
  }

  public outline(angleDegrees: number, samples = 192, destination?: Float32Array): Float32Array {
    const half = Math.max(8, Math.floor(samples / 2)); const length = 4 * half;
    if (destination !== undefined && destination.length < length) throw new RangeError("foil outline destination is too small");
    this.updateRotation(angleDegrees); const output = destination?.subarray(0, length) ?? new Float32Array(length); const cosine = this.cachedCosine; const sine = this.cachedSine;
    const write = (index: number, localX: number, localY: number): void => { const shiftedX = localX - 0.25 * this.spec.chord; output[2 * index] = cosine * shiftedX - sine * localY + this.pivotX; output[2 * index + 1] = sine * shiftedX + cosine * localY + this.pivotY; };
    for (let index = 0; index < half; index += 1) { const beta = Math.PI * index / (half - 1); const x = this.spec.chord * 0.5 * (1 - Math.cos(beta)); this.updateSurfaces(x); write(index, x, this.surfaceUpper); }
    for (let index = 0; index < half; index += 1) { const beta = Math.PI * (half - 1 - index) / (half - 1); const x = this.spec.chord * 0.5 * (1 - Math.cos(beta)); this.updateSurfaces(x); write(half + index, x, this.surfaceLower); }
    return output;
  }
}
