import { describe, expect, test, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChangelog } from "../use-changelog";
import type { SeenStateAdapter } from "../seen-state";
import { LATEST_ENTRY } from "../manifest";

function makeAdapter(initial: string | null = null): SeenStateAdapter {
  let stored = initial;
  return {
    read: vi.fn(async () => stored),
    write: vi.fn(async (id: string) => {
      stored = id;
    }),
  };
}

async function flushAndAdvance() {
  // Flush microtasks (resolves adapter.read() promise → sets hydrated)
  await act(async () => {
    await Promise.resolve();
  });
  // Advance fake timers (fires the PILL_DELAY_MS setTimeout)
  await act(async () => {
    vi.advanceTimersByTime(1000);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
      key: () => null,
      length: 0,
    },
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useChangelog", () => {
  test("pillVisible becomes true after delay when there is an unread entry", async () => {
    const seenState = makeAdapter(null);
    const { result } = renderHook(() => useChangelog({ seenState }));
    expect(result.current.pillVisible).toBe(false);

    await flushAndAdvance();
    expect(result.current.pillVisible).toBe(true);
  });

  test("pillVisible stays false when latest entry already seen", async () => {
    const seenState = makeAdapter(LATEST_ENTRY.id);
    const { result } = renderHook(() => useChangelog({ seenState }));
    await flushAndAdvance();
    expect(result.current.pillVisible).toBe(false);
  });

  test("pillVisible stays false when hidden=true", async () => {
    const seenState = makeAdapter(null);
    const { result } = renderHook(() =>
      useChangelog({ seenState, hidden: true }),
    );
    await flushAndAdvance();
    expect(result.current.pillVisible).toBe(false);
  });

  test("setOpen(true) hides pill and calls onOpen", async () => {
    const seenState = makeAdapter(null);
    const onOpen = vi.fn();
    const { result } = renderHook(() => useChangelog({ seenState, onOpen }));
    await flushAndAdvance();

    act(() => {
      result.current.setOpen(true);
    });
    expect(result.current.open).toBe(true);
    expect(result.current.pillVisible).toBe(false);
    expect(onOpen).toHaveBeenCalledOnce();
  });

  test("dismissPill hides pill and marks seen", async () => {
    const seenState = makeAdapter(null);
    const { result } = renderHook(() => useChangelog({ seenState }));
    await flushAndAdvance();
    expect(result.current.pillVisible).toBe(true);

    act(() => {
      result.current.dismissPill();
    });
    expect(result.current.pillVisible).toBe(false);
    expect(seenState.write).toHaveBeenCalledWith(LATEST_ENTRY.id);
  });

  test("setActiveId calls onEntryView callback", async () => {
    const seenState = makeAdapter(LATEST_ENTRY.id);
    const onEntryView = vi.fn();
    const { result } = renderHook(() =>
      useChangelog({ seenState, onEntryView }),
    );
    await flushAndAdvance();

    act(() => {
      result.current.setActiveId("2026-04-09");
    });
    expect(onEntryView).toHaveBeenCalledWith("2026-04-09");
  });

  test("hasUnread is true when lastSeenId differs from latest", async () => {
    const seenState = makeAdapter("2026-04-09");
    const { result } = renderHook(() => useChangelog({ seenState }));
    await flushAndAdvance();
    expect(result.current.hasUnread).toBe(true);
  });

  test("hasUnread is false when lastSeenId matches latest", async () => {
    const seenState = makeAdapter(LATEST_ENTRY.id);
    const { result } = renderHook(() => useChangelog({ seenState }));
    await flushAndAdvance();
    expect(result.current.hasUnread).toBe(false);
  });
});
