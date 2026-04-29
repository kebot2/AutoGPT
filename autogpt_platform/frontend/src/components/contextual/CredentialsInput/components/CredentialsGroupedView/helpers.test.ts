import { describe, expect, it, vi } from "vitest";
import type { CredentialsMetaResponse } from "@/lib/autogpt-server-api";
import type { CredentialsProvidersContextType } from "@/providers/agent-credentials/credentials-provider";
import { findSavedCredentialByProviderAndType } from "./helpers";

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

function makeProviders(
  savedCredentials: CredentialsMetaResponse[],
): CredentialsProvidersContextType {
  return {
    reddit: {
      provider: "reddit",
      providerName: "Reddit",
      savedCredentials,
      isSystemProvider: true,
      oAuthCallback: vi.fn(),
      mcpOAuthCallback: vi.fn(),
      createAPIKeyCredentials: vi.fn(),
      createUserPasswordCredentials: vi.fn(),
      createHostScopedCredentials: vi.fn(),
      deleteCredentials: vi.fn(),
    },
  };
}

describe("findSavedCredentialByProviderAndType", () => {
  it("accepts wildcard-scoped oauth credentials for required scopes", () => {
    const providers = makeProviders([
      makeCredential({
        id: "wild",
        scopes: ["*"],
        is_system: true,
      }),
    ]);

    const credential = findSavedCredentialByProviderAndType(
      ["reddit"],
      ["oauth2"],
      ["modposts"],
      providers,
    );

    expect(credential?.id).toBe("wild");
  });
});
