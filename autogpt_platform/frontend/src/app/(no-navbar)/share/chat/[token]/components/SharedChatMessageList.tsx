import type { SharedChatLinkedExecution } from "@/app/api/__generated__/models/sharedChatLinkedExecution";
import type { SharedChatMessage } from "@/app/api/__generated__/models/sharedChatMessage";
import { executionSharePath } from "@/lib/share/routes";

type Props = {
  messages: SharedChatMessage[];
  linkedExecutions: SharedChatLinkedExecution[];
};

export function SharedChatMessageList({ messages, linkedExecutions }: Props) {
  // Pre-index linked executions so we can attach drill-in links when a
  // tool message references an execution that was opted-in at share time.
  const sharedExecutionTokens = new Map<string, string>();
  for (const link of linkedExecutions) {
    if (link.share_token) {
      sharedExecutionTokens.set(link.execution_id, link.share_token);
    }
  }

  if (messages.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 px-4 py-8 text-center text-sm text-zinc-500">
        This chat has no messages yet.
      </div>
    );
  }

  return (
    <ol className="space-y-4">
      {messages.map((message) => (
        <li
          key={message.id || `seq-${message.sequence}`}
          className="rounded-lg border border-zinc-200 bg-white px-4 py-3"
        >
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
              {labelForRole(message.role)}
            </span>
            <span className="text-xs text-zinc-400">
              {new Date(message.created_at).toLocaleTimeString()}
            </span>
          </div>
          {message.content && (
            <div className="whitespace-pre-wrap text-sm text-zinc-800">
              {message.content}
            </div>
          )}
          {message.role === "tool" &&
            renderExecutionDrillIn(message, sharedExecutionTokens)}
        </li>
      ))}
    </ol>
  );
}

function labelForRole(role: string): string {
  switch (role) {
    case "user":
      return "You";
    case "assistant":
      return "AutoGPT";
    case "tool":
      return "Tool result";
    default:
      return role;
  }
}

function renderExecutionDrillIn(
  message: SharedChatMessage,
  sharedExecutionTokens: Map<string, string>,
) {
  // Tool messages serialise the response body as JSON in content.
  // When the response is an ``execution_started`` payload and the chat
  // share opted that execution in, surface a link to the public
  // execution viewer.
  if (!message.content) return null;
  try {
    const parsed = JSON.parse(message.content) as {
      type?: string;
      execution_id?: string;
    };
    if (parsed?.type !== "execution_started" || !parsed.execution_id) {
      return null;
    }
    const token = sharedExecutionTokens.get(parsed.execution_id);
    if (!token) return null;
    return (
      <a
        href={executionSharePath(token)}
        className="mt-2 inline-block text-xs text-blue-700 underline hover:text-blue-900"
      >
        View full agent execution →
      </a>
    );
  } catch {
    return null;
  }
}
