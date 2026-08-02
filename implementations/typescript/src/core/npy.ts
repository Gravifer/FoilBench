import type {FloatArray, Precision} from "./contracts.js";

export interface NpyArray {
  readonly precision: Precision;
  readonly shape: readonly number[];
  readonly fortranOrder: boolean;
  readonly data: FloatArray;
}

function parseShape(header: string): number[] {
  const match = /'shape':\s*\(([^)]*)\)/.exec(header);
  if (match?.[1] === undefined) throw new TypeError("NPY shape is missing");
  return match[1].split(",").map((part) => part.trim()).filter(Boolean).map(Number);
}

export function decodeNpy(buffer: ArrayBuffer): NpyArray {
  const bytes = new Uint8Array(buffer);
  if (String.fromCharCode(...bytes.slice(1, 6)) !== "NUMPY") throw new TypeError("invalid NPY magic");
  const major = bytes[6];
  if (major === undefined) throw new TypeError("NPY version is missing");
  const view = new DataView(buffer);
  const headerLength = major === 1 ? view.getUint16(8, true) : view.getUint32(8, true);
  const headerStart = major === 1 ? 10 : 12;
  const header = new TextDecoder("latin1").decode(bytes.slice(headerStart, headerStart + headerLength));
  const descriptor = /'descr':\s*'([^']+)'/.exec(header)?.[1];
  if (descriptor !== "<f4" && descriptor !== "<f8") throw new TypeError(`unsupported NPY dtype ${descriptor ?? "missing"}`);
  const shape = parseShape(header);
  const count = shape.reduce((left, right) => left * right, 1);
  const precision: Precision = descriptor === "<f4" ? "float32" : "float64";
  const data: FloatArray = precision === "float32" ? new Float32Array(count) : new Float64Array(count);
  const width = precision === "float32" ? 4 : 8;
  const dataOffset = headerStart + headerLength;
  for (let index = 0; index < count; index += 1) data[index] = precision === "float32" ? view.getFloat32(dataOffset + index * width, true) : view.getFloat64(dataOffset + index * width, true);
  return {precision, shape, fortranOrder: /'fortran_order':\s*True/.test(header), data};
}

export function semanticCOrder(array: NpyArray): FloatArray {
  if (!array.fortranOrder) return array.data.slice();
  const output: FloatArray = array.precision === "float32" ? new Float32Array(array.data.length) : new Float64Array(array.data.length);
  const dimensions = array.shape.length;
  const coordinates = new Array<number>(dimensions).fill(0);
  for (let cIndex = 0; cIndex < output.length; cIndex += 1) {
    let remainder = cIndex;
    for (let axis = dimensions - 1; axis >= 0; axis -= 1) {
      const size = array.shape[axis] ?? 1;
      coordinates[axis] = remainder % size;
      remainder = Math.floor(remainder / size);
    }
    let fIndex = 0; let stride = 1;
    for (let axis = 0; axis < dimensions; axis += 1) {
      fIndex += (coordinates[axis] ?? 0) * stride;
      stride *= array.shape[axis] ?? 1;
    }
    output[cIndex] = array.data[fIndex] ?? Number.NaN;
  }
  return output;
}
