import { describe, expect, it } from "vitest";
import type { CredentialsMetaResponse } from "@/app/api/__generated__/models/credentialsMetaResponse";
import type { BlockIOCredentialsSubSchema } from "@/lib/autogpt-server-api";
import { filterCredentialsByProvider } from "./helpers";

function makeCredential(
  partial: Partial<CredentialsMetaResponse>,
): CredentialsMetaResponse {
  return {
    id: "cred-id",
    provider: "reddit",
    type: "oauth2",
    title: "Reddit credential",
    scopes: [],
    ...partial,
  } as CredentialsMetaResponse;
}

function makeSchema(
  partial: Partial<BlockIOCredentialsSubSchema> = {},
): BlockIOCredentialsSubSchema {
  return {
    credentials_provider: ["reddit"],
    credentials_types: ["oauth2"],
    credentials_scopes: ["modposts"],
    ...partial,
  } as BlockIOCredentialsSubSchema;
}

describe("filterCredentialsByProvider", () => {
  it("keeps wildcard-scoped oauth credentials when scopes are required", () => {
    const result = filterCredentialsByProvider(
      [makeCredential({ id: "wild", scopes: ["*"] })],
      "reddit",
      makeSchema(),
    );

    expect(result.exists).toBe(true);
    expect(result.credentials.map((credential) => credential.id)).toEqual([
      "wild",
    ]);
  });

  it("filters oauth credentials that still lack a required scope", () => {
    const result = filterCredentialsByProvider(
      [makeCredential({ id: "narrow", scopes: ["read"] })],
      "reddit",
      makeSchema(),
    );

    expect(result.exists).toBe(false);
    expect(result.credentials).toEqual([]);
  });
});
