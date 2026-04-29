import { describe, expect, it } from "vitest";
import { hasRequiredCredentialScopes } from "./hasRequiredCredentialScopes";

describe("hasRequiredCredentialScopes", () => {
  it("returns true when no scopes are required", () => {
    expect(hasRequiredCredentialScopes(["read"], undefined)).toBe(true);
    expect(hasRequiredCredentialScopes(["read"], [])).toBe(true);
  });

  it("treats wildcard scopes as satisfying all requirements", () => {
    expect(
      hasRequiredCredentialScopes(["*"], ["modposts", "modcontributors"]),
    ).toBe(true);
  });

  it("returns false when a required scope is missing", () => {
    expect(hasRequiredCredentialScopes(["read"], ["read", "modposts"])).toBe(
      false,
    );
  });
});
