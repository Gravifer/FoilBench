import type {FoilSpec} from "./contracts.js";

export class NacaFoil {
  private readonly pivotX: number;
  private readonly pivotY: number;

  public constructor(public readonly spec: FoilSpec) {
    if (!/^\d{4}$/.test(spec.naca)) throw new TypeError("NACA code must have four digits");
    const pivotX = spec.pivot[0]; const pivotY = spec.pivot[1];
    if (pivotX === undefined || pivotY === undefined) throw new RangeError("2D foil pivot is required");
    this.pivotX = pivotX; this.pivotY = pivotY;
  }

  private surfaces(x: number): readonly [number, number] {
    const m = Number(this.spec.naca[0]) / 100;
    const p = Number(this.spec.naca[1]) / 10;
    const thickness = Number(this.spec.naca.slice(2)) / 100;
    const selected = Math.max(0, Math.min(1, x / this.spec.chord));
    const yt = 5 * thickness * this.spec.chord * (0.2969 * Math.sqrt(selected) - 0.126 * selected - 0.3516 * selected ** 2 + 0.2843 * selected ** 3 - 0.1036 * selected ** 4);
    let yc = 0;
    if (m > 0 && p > 0 && selected < p) yc = m / (p * p) * (2 * p * selected - selected ** 2);
    else if (m > 0 && p < 1) yc = m / ((1 - p) ** 2) * ((1 - 2 * p) + 2 * p * selected - selected ** 2);
    return [yc * this.spec.chord + yt, yc * this.spec.chord - yt];
  }

  public signedDistance(x: number, y: number, angleDegrees: number): number {
    const angle = angleDegrees * Math.PI / 180;
    const dx = x - this.pivotX; const dy = y - this.pivotY;
    const localX = Math.cos(angle) * dx + Math.sin(angle) * dy + 0.25 * this.spec.chord;
    const localY = -Math.sin(angle) * dx + Math.cos(angle) * dy;
    const [upper, lower] = this.surfaces(localX);
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
      const angle = angleDegrees * Math.PI / 180;
      dx = -Math.sin(angle); dy = Math.cos(angle); length = 1;
    }
    return [dx / Math.max(length, epsilon), dy / Math.max(length, epsilon)];
  }

  public outline(angleDegrees: number, samples = 192): Float32Array {
    const half = Math.max(8, Math.floor(samples / 2)); const output = new Float32Array(4 * half); const angle = angleDegrees * Math.PI / 180;
    const write = (index: number, localX: number, localY: number): void => { const shiftedX = localX - 0.25 * this.spec.chord; output[2 * index] = Math.cos(angle) * shiftedX - Math.sin(angle) * localY + this.pivotX; output[2 * index + 1] = Math.sin(angle) * shiftedX + Math.cos(angle) * localY + this.pivotY; };
    for (let index = 0; index < half; index += 1) { const beta = Math.PI * index / (half - 1); const x = this.spec.chord * 0.5 * (1 - Math.cos(beta)); write(index, x, this.surfaces(x)[0]); }
    for (let index = 0; index < half; index += 1) { const beta = Math.PI * (half - 1 - index) / (half - 1); const x = this.spec.chord * 0.5 * (1 - Math.cos(beta)); write(half + index, x, this.surfaces(x)[1]); }
    return output;
  }
}
