export type LatestRequestResult<T> =
  | {readonly current: true; readonly value: T}
  | {readonly current: false};

export class LatestRequestGate {
  private generation = 0;

  async run<T>(operation: () => Promise<T>): Promise<LatestRequestResult<T>> {
    const generation = ++this.generation;
    try {
      const value = await operation();
      return generation === this.generation ? {current: true, value} : {current: false};
    } catch (reason) {
      if (generation === this.generation) throw reason;
      return {current: false};
    }
  }

  invalidate(): void {
    this.generation += 1;
  }
}
