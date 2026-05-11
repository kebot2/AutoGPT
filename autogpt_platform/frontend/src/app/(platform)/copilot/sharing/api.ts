// Thin typed wrappers around the chat-share endpoints.  These match
// the response shapes returned by ``backend/api/features/chat/share.py``
// and exist because the OpenAPI client (orval) hasn't been regenerated
// against the new routes yet.  When regenerated hooks land, swap the
// callers below over to those and delete this file.

import { customMutator } from "@/app/api/mutators/custom-mutator";
import { shareApiPaths } from "@/lib/share/routes";

export interface SharedChatLinkedExecution {
  execution_id: string;
  graph_id: string;
  graph_name: string | null;
  share_token: string | null;
}

export interface ListLinkedExecutionsResponse {
  linked_executions: SharedChatLinkedExecution[];
  is_shared: boolean;
  share_token: string | null;
}

export interface EnableChatShareBody {
  linked_execution_ids: string[];
}

export interface ShareResponse {
  share_url: string;
  share_token: string;
}

export interface SharedChatMessage {
  id: string;
  role: string;
  content: string | null;
  name: string | null;
  tool_call_id: string | null;
  tool_calls: unknown[] | null;
  function_call: unknown | null;
  sequence: number;
  created_at: string;
}

export interface SharedChatSession {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  linked_executions: SharedChatLinkedExecution[];
}

export interface SharedChatMessagesPage {
  messages: SharedChatMessage[];
  has_more: boolean;
  oldest_sequence: number | null;
}

interface ApiEnvelope<T> {
  data: T;
  status: number;
  headers: Headers;
}

export async function fetchLinkedExecutions(
  sessionId: string,
): Promise<ListLinkedExecutionsResponse> {
  const res = await customMutator<ApiEnvelope<ListLinkedExecutionsResponse>>(
    shareApiPaths.chatLinkedExecutions(sessionId),
    { method: "GET" },
  );
  return res.data;
}

export async function enableChatShareApi(
  sessionId: string,
  body: EnableChatShareBody,
): Promise<ShareResponse> {
  const res = await customMutator<ApiEnvelope<ShareResponse>>(
    shareApiPaths.enableChat(sessionId),
    { method: "POST", body: JSON.stringify(body) },
  );
  return res.data;
}

export async function disableChatShareApi(sessionId: string): Promise<void> {
  await customMutator<ApiEnvelope<null>>(shareApiPaths.disableChat(sessionId), {
    method: "DELETE",
  });
}

export async function fetchSharedChat(
  shareToken: string,
): Promise<SharedChatSession> {
  const res = await customMutator<ApiEnvelope<SharedChatSession>>(
    shareApiPaths.getSharedChat(shareToken),
    { method: "GET" },
  );
  return res.data;
}

export async function fetchSharedChatMessages(
  shareToken: string,
  params: { limit?: number; before_sequence?: number } = {},
): Promise<SharedChatMessagesPage> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.before_sequence !== undefined)
    search.set("before_sequence", String(params.before_sequence));
  const qs = search.toString();
  const url = qs
    ? `${shareApiPaths.getSharedChatMessages(shareToken)}?${qs}`
    : shareApiPaths.getSharedChatMessages(shareToken);
  const res = await customMutator<ApiEnvelope<SharedChatMessagesPage>>(url, {
    method: "GET",
  });
  return res.data;
}
