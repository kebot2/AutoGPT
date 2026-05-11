"use client";

import { useParams } from "next/navigation";
import { useSharedChatPage } from "./useSharedChatPage";
import { SharedChatMessageList } from "./components/SharedChatMessageList";
import { SharedChatErrorState } from "./components/SharedChatErrorState";
import { SharedChatLoadingState } from "./components/SharedChatLoadingState";

export default function SharedChatPage() {
  const params = useParams();
  const token = params.token as string;

  const { session, messages, isLoading, isError, error, retry } =
    useSharedChatPage(token);

  if (isLoading) {
    return <SharedChatLoadingState />;
  }

  if (isError || !session) {
    return <SharedChatErrorState reason={error} onRetry={retry} />;
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6 space-y-1">
        <h1 className="text-2xl font-semibold">
          {session.title || "Shared chat"}
        </h1>
        <p className="text-sm text-zinc-500">
          Shared on {new Date(session.created_at).toLocaleDateString()} · view
          only
        </p>
      </header>

      <div className="mb-6 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        This is a public read-only view of a chat conversation. The person who
        shared it can revoke access at any time.
      </div>

      <SharedChatMessageList
        messages={messages}
        linkedExecutions={session.linked_executions}
      />

      <div className="mt-12 text-center text-xs text-zinc-400">
        Powered by AutoGPT Platform
      </div>
    </div>
  );
}
