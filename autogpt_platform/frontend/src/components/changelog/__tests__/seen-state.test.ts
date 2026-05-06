import { describe, expect, test, beforeEach, vi } from "vitest";
import { localStorageAdapter, userPrefsAdapter } from "../seen-state";

const STORAGE_KEY = "autogpt:changelog:lastSeenId";

function makeStorage(): Storage {
  const store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { Object.keys(store).forEach((k) => delete store[k]); },
    key: (i: number) => Object.keys(store)[i] ?? null,
    get length() { return Object.keys(store).length; },
  };
}

beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    value: makeStorage(),
    writable: true,
    configurable: true,
  });
});

describe("localStorageAdapter", () => {
  test("read() returns null when nothing stored", async () => {
    expect(await localStorageAdapter.read()).toBeNull();
  });

  test("write() then read() round-trips the value", async () => {
    await localStorageAdapter.write("2026-05-01");
    expect(await localStorageAdapter.read()).toBe("2026-05-01");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("2026-05-01");
  });

  test("read() returns null when localStorage throws", async () => {
    Object.defineProperty(globalThis, "localStorage", {
      get() { throw new Error("no storage"); },
      configurable: true,
    });
    expect(await localStorageAdapter.read()).toBeNull();
  });

  test("write() is silent when localStorage throws", async () => {
    Object.defineProperty(globalThis, "localStorage", {
      get() { throw new Error("no storage"); },
      configurable: true,
    });
    await expect(localStorageAdapter.write("2026-05-01")).resolves.toBeUndefined();
  });
});

describe("userPrefsAdapter", () => {
  test("read() fetches remote on first call and caches result", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ lastSeenId: "2026-05-01" }),
    });
    const adapter = userPrefsAdapter({ fetchFn });
    const result = await adapter.read();
    expect(result).toBe("2026-05-01");
    expect(fetchFn).toHaveBeenCalledOnce();
    expect(localStorage.getItem(STORAGE_KEY)).toBe("2026-05-01");
  });

  test("read() returns cached value on subsequent calls", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ lastSeenId: "2026-05-01" }),
    });
    const adapter = userPrefsAdapter({ fetchFn });
    await adapter.read();
    fetchFn.mockClear();
    const second = await adapter.read();
    expect(second).toBe("2026-05-01");
  });

  test("read() returns null when remote returns null lastSeenId", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ lastSeenId: null }),
    });
    const adapter = userPrefsAdapter({ fetchFn });
    expect(await adapter.read()).toBeNull();
  });

  test("read() returns null when remote errors", async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error("network error"));
    const adapter = userPrefsAdapter({ fetchFn });
    expect(await adapter.read()).toBeNull();
  });

  test("read() returns null when remote returns non-ok status", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    const adapter = userPrefsAdapter({ fetchFn });
    expect(await adapter.read()).toBeNull();
  });

  test("write() updates cache, localStorage, and calls PUT", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({}),
    });
    const adapter = userPrefsAdapter({ endpoint: "/api/prefs", fetchFn });
    await adapter.write("2026-05-01");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("2026-05-01");
    expect(fetchFn).toHaveBeenCalledWith("/api/prefs", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ lastSeenId: "2026-05-01" }),
    }));
  });

  test("write() is non-fatal when PUT errors", async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error("offline"));
    const adapter = userPrefsAdapter({ fetchFn });
    await expect(adapter.write("2026-05-01")).resolves.toBeUndefined();
    expect(localStorage.getItem(STORAGE_KEY)).toBe("2026-05-01");
  });

  test("read() uses localStorage cache if already set", async () => {
    localStorage.setItem(STORAGE_KEY, "2026-04-09");
    const fetchFn = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ lastSeenId: "2026-05-01" }),
    });
    const adapter = userPrefsAdapter({ fetchFn });
    const result = await adapter.read();
    expect(result).toBe("2026-04-09");
  });
});
