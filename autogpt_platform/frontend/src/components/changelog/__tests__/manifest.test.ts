import { describe, expect, test } from "vitest";
import { CHANGELOG_MANIFEST, LATEST_ENTRY } from "../manifest";

describe("CHANGELOG_MANIFEST", () => {
  test("is non-empty", () => {
    expect(CHANGELOG_MANIFEST.length).toBeGreaterThan(0);
  });

  test("every entry has required fields", () => {
    for (const entry of CHANGELOG_MANIFEST) {
      expect(typeof entry.id).toBe("string");
      expect(typeof entry.slug).toBe("string");
      expect(typeof entry.dateLabel).toBe("string");
      expect(typeof entry.title).toBe("string");
      expect(Array.isArray(entry.versions)).toBe(true);
      expect(entry.versions.length).toBeGreaterThan(0);
    }
  });

  test("ids are unique", () => {
    const ids = CHANGELOG_MANIFEST.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  test("slugs are unique", () => {
    const slugs = CHANGELOG_MANIFEST.map((e) => e.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  test("LATEST_ENTRY equals the first manifest entry", () => {
    expect(LATEST_ENTRY).toBe(CHANGELOG_MANIFEST[0]);
  });
});
