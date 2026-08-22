import {describe, expect, it, vi} from "vitest";

import type {NoticeScheduler} from "../src/statusNotice.js";
import {StatusNoticeController} from "../src/statusNotice.js";

describe("StatusNoticeController", () => {
  it("restarts a repeated message when its event identity changes", () => {
    const callbacks: Array<() => void> = [];
    const cancellations: boolean[] = [];
    const delays: number[] = [];
    const schedule: NoticeScheduler = (callback, delay) => {
      const index = callbacks.push(callback) - 1;
      cancellations[index] = false;
      delays.push(delay);
      return () => { cancellations[index] = true; };
    };
    const publish = vi.fn<(notice: string | null) => void>();
    const notices = new StatusNoticeController(publish, schedule);

    notices.show("command:1", "manual control", 8000);
    notices.show("command:2", "manual control", 8000);

    expect(delays).toEqual([8000, 8000]);
    expect(cancellations).toEqual([true, false]);
    callbacks[0]?.();
    expect(publish).toHaveBeenLastCalledWith("manual control");
    callbacks[1]?.();
    expect(publish).toHaveBeenLastCalledWith(null);
  });

  it("does not reschedule the same status event", () => {
    const schedule = vi.fn<NoticeScheduler>(() => () => undefined);
    const notices = new StatusNoticeController(() => undefined, schedule);
    notices.show("command:1", "manual control", 8000);
    notices.show("command:1", "manual control", 8000);
    expect(schedule).toHaveBeenCalledTimes(1);
  });
});
