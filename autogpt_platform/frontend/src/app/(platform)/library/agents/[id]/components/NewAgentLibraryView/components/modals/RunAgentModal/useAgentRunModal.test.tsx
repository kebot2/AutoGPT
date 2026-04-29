import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LibraryAgent } from "@/app/api/__generated__/models/libraryAgent";
import type { CredentialsMetaResponse } from "@/lib/autogpt-server-api";
import {
  CredentialsProvidersContext,
  type CredentialsProvidersContextType,
} from "@/providers/agent-credentials/credentials-provider";
import { useAgentRunModal } from "./useAgentRunModal";

const executeGraphMutate = vi.fn();
const setupTriggerMutate = vi.fn();
const invalidateQueries = vi.fn();
const toast = vi.fn();

vi.mock("@/app/api/__generated__/endpoints/graphs/graphs", () => ({
  getGetV1ListGraphExecutionsQueryKey: vi.fn(() => ["graph-executions"]),
  usePostV1ExecuteGraphAgent: vi.fn(() => ({
    mutate: executeGraphMutate,
    isPending: false,
  })),
}));

vi.mock("@/app/api/__generated__/endpoints/presets/presets", () => ({
  getGetV2ListPresetsQueryKey: vi.fn(() => ["presets"]),
  usePostV2SetupTrigger: vi.fn(() => ({
    mutate: setupTriggerMutate,
    isPending: false,
  })),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: vi.fn(() => ({
    invalidateQueries,
  })),
}));

vi.mock("@/components/molecules/Toast/use-toast", () => ({
  useToast: vi.fn(() => ({
    toast,
  })),
}));

vi.mock("@/services/analytics", () => ({
  analytics: {
    sendDatafastEvent: vi.fn(),
  },
}));

vi.mock("./errorHelpers", () => ({
  showExecutionErrorToast: vi.fn(),
}));

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

function makeAgent(): LibraryAgent {
  return {
    id: "agent-id",
    graph_id: "agent-graph-id",
    graph_version: 1,
    name: "Reddit moderation agent",
    input_schema: {
      properties: {},
      required: [],
    },
    credentials_input_schema: {
      properties: {
        reddit_credentials: {
          credentials_provider: ["reddit"],
          credentials_types: ["oauth2"],
          credentials_scopes: ["modposts"],
        },
      },
      required: ["reddit_credentials"],
    },
    trigger_setup_info: null,
  } as unknown as LibraryAgent;
}

describe("useAgentRunModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("initializes wildcard-scoped system credentials as matching", async () => {
    const providers = makeProviders([
      makeCredential({
        id: "wild",
        title: "Wildcard Reddit credential",
        scopes: ["*"],
        is_system: true,
      }),
    ]);

    function Wrapper({ children }: { children: React.ReactNode }) {
      return React.createElement(
        CredentialsProvidersContext.Provider,
        { value: providers },
        children,
      );
    }

    const { result } = renderHook(() => useAgentRunModal(makeAgent()), {
      wrapper: Wrapper,
    });

    await waitFor(() =>
      expect(result.current.inputCredentials.reddit_credentials).toMatchObject({
        id: "wild",
        provider: "reddit",
        type: "oauth2",
      }),
    );
  });
});
