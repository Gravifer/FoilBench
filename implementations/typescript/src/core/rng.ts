const MASK64 = (1n << 64n) - 1n;
const MULTIPLIER = 6364136223846793005n;

export class Pcg32 {
  private state = 0n;
  private readonly increment: bigint;

  public constructor(seed: number, stream = 54) {
    if (!Number.isSafeInteger(seed) || seed < 0 || seed > 0xffff_ffff) throw new RangeError("seed must be uint32");
    if (!Number.isSafeInteger(stream) || stream < 0) throw new RangeError("stream must be nonnegative");
    this.increment = ((BigInt(stream) << 1n) | 1n) & MASK64;
    this.nextUint32();
    this.state = (this.state + BigInt(seed)) & MASK64;
    this.nextUint32();
  }

  public nextUint32(): number {
    const old = this.state;
    this.state = (old * MULTIPLIER + this.increment) & MASK64;
    const xorshifted = Number((((old >> 18n) ^ old) >> 27n) & 0xffff_ffffn) >>> 0;
    const rotation = Number((old >> 59n) & 31n);
    return ((xorshifted >>> rotation) | (xorshifted << ((-rotation) & 31))) >>> 0;
  }

  public nextFloat32(): number {
    return Math.fround(Math.fround(this.nextUint32()) * Math.fround(2 ** -32));
  }

  public checkpoint(): bigint { return this.state; }

  public restore(checkpoint: bigint): void {
    if (checkpoint < 0n || checkpoint > MASK64) throw new RangeError("PCG32 checkpoint must be uint64");
    this.state = checkpoint;
  }

  public fill(target: Float32Array): void {
    for (let index = 0; index < target.length; index += 1) target[index] = this.nextFloat32();
  }
}
