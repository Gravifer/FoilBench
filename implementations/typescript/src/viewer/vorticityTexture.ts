export function vorticityRgba(
  vorticity: Float32Array,
  nx: number,
  ny: number,
): Uint8ClampedArray {
  if (nx <= 0 || ny <= 0 || vorticity.length !== nx * ny) {
    throw new RangeError("vorticity texture dimensions disagree with the field");
  }
  const output = new Uint8ClampedArray(4 * vorticity.length);
  for (let y = 0; y < ny; y += 1) for (let x = 0; x < nx; x += 1) {
    const source = y * nx + x;
    const destination = (ny - 1 - y) * nx + x;
    const normalized = Math.max(-1, Math.min(1, vorticity[source] ?? 0));
    const magnitude = Math.abs(normalized) ** 0.7;
    const ramp = Math.max(0, Math.min(1, (magnitude - 0.18) / (0.9 - 0.18)));
    const visibility = ramp * ramp * (3 - 2 * ramp);
    output[4 * destination] = Math.round(255 * (normalized >= 0 ? 0.65 : 0.02));
    output[4 * destination + 1] = Math.round(255 * (normalized >= 0 ? 0.12 : 0.28));
    output[4 * destination + 2] = Math.round(255 * (normalized >= 0 ? 0.02 : 0.65));
    output[4 * destination + 3] = Math.round(255 * 0.38 * visibility);
  }
  return output;
}
