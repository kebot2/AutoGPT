// Single source of truth for every share-related path: the viewer
// route that ends up on a user's clipboard, and the API path the
// frontend hits when toggling shares.  When the next shareable type
// joins the platform, add functions here and grep won't find anything
// else to touch.

function getBaseUrl(): string {
  // ``NEXT_PUBLIC_FRONTEND_BASE_URL`` lets the backend's share URL and
  // the frontend's share URL match in environments where Next.js is
  // proxied behind a different host than its window.location.  Falls
  // back to the current origin in dev.
  if (typeof window === "undefined") {
    return process.env.NEXT_PUBLIC_FRONTEND_BASE_URL || "";
  }
  return process.env.NEXT_PUBLIC_FRONTEND_BASE_URL || window.location.origin;
}

export function executionSharePath(token: string): string {
  return `/share/${token}`;
}

export function chatSharePath(token: string): string {
  return `/share/chat/${token}`;
}

export function executionShareUrl(token: string): string {
  return `${getBaseUrl()}${executionSharePath(token)}`;
}

export function chatShareUrl(token: string): string {
  return `${getBaseUrl()}${chatSharePath(token)}`;
}

// API paths used by the chat-share hooks until orval regenerates against
// the updated backend OpenAPI spec.  Once generated hooks exist for these
// operations, the hooks below should be replaced and these consts removed.
export const shareApiPaths = {
  enableChat: (sessionId: string) => `/api/chat/sessions/${sessionId}/share`,
  disableChat: (sessionId: string) => `/api/chat/sessions/${sessionId}/share`,
  chatLinkedExecutions: (sessionId: string) =>
    `/api/chat/sessions/${sessionId}/share/linked-executions`,
  getSharedChat: (token: string) => `/api/public/shared/chats/${token}`,
  getSharedChatMessages: (token: string) =>
    `/api/public/shared/chats/${token}/messages`,
};
