import { useQuery } from "@tanstack/react-query";
import {
  fetchSharedChat,
  fetchSharedChatMessages,
} from "@/app/(platform)/copilot/sharing/api";

const PAGE_SIZE = 200;

export function useSharedChatPage(token: string) {
  const sessionQuery = useQuery({
    queryKey: ["shared-chat", token, "session"],
    queryFn: () => fetchSharedChat(token),
    retry: false,
  });

  const messagesQuery = useQuery({
    queryKey: ["shared-chat", token, "messages"],
    queryFn: () => fetchSharedChatMessages(token, { limit: PAGE_SIZE }),
    enabled: !!sessionQuery.data,
    retry: false,
  });

  const isLoading = sessionQuery.isLoading || messagesQuery.isLoading;
  const isError = sessionQuery.isError || messagesQuery.isError;
  const error =
    (sessionQuery.error || messagesQuery.error) instanceof Error
      ? (sessionQuery.error || messagesQuery.error)!.message
      : undefined;

  return {
    session: sessionQuery.data,
    messages: messagesQuery.data?.messages ?? [],
    hasMore: messagesQuery.data?.has_more ?? false,
    isLoading,
    isError,
    error,
    retry: () => {
      sessionQuery.refetch();
      messagesQuery.refetch();
    },
  };
}
