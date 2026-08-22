export type NoticeScheduler = (callback: () => void, delayMilliseconds: number) => () => void;

const browserScheduler: NoticeScheduler = (callback, delayMilliseconds) => {
  const timer = window.setTimeout(callback, delayMilliseconds);
  return () => window.clearTimeout(timer);
};

export class StatusNoticeController {
  private identity: string | null = null;
  private cancelPending: (() => void) | null = null;

  constructor(
    private readonly publish: (notice: string | null) => void,
    private readonly schedule: NoticeScheduler = browserScheduler,
  ) {}

  show(identity: string, notice: string, durationMilliseconds: number): void {
    if (identity === this.identity) return;
    this.identity = identity;
    this.cancelPending?.();
    this.publish(notice);
    this.cancelPending = this.schedule(() => {
      if (this.identity !== identity) return;
      this.publish(null);
      this.cancelPending = null;
    }, durationMilliseconds);
  }

  dispose(): void {
    this.cancelPending?.();
    this.cancelPending = null;
  }
}
