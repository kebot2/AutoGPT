import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useToast } from "@/components/molecules/Toast/use-toast";
import { chatShareUrl } from "@/lib/share/routes";
import {
  disableChatShareApi,
  enableChatShareApi,
  fetchLinkedExecutions,
  SharedChatLinkedExecution,
} from "./api";

type Props = {
  sessionId: string;
  open: boolean;
};

export function useShareChatDialog({ sessionId, open }: Props) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [isShared, setIsShared] = useState(false);
  const [shareToken, setShareToken] = useState<string | null>(null);
  const [selectedExecutionIds, setSelectedExecutionIds] = useState<Set<string>>(
    new Set(),
  );
  const [copied, setCopied] = useState(false);

  const linkedExecutionsKey = ["chat-share-linked-executions", sessionId];

  // Only load linked executions when the modal opens — the scan can touch
  // many tool messages, so we don't run it eagerly on chat mount.
  const { data: linkedExecutions, isLoading: isLoadingLinks } = useQuery({
    queryKey: linkedExecutionsKey,
    queryFn: () => fetchLinkedExecutions(sessionId),
    enabled: open,
  });

  // Hydrate local share state from the backend payload so the modal
  // opens in the right mode after a reload.
  useEffect(() => {
    if (linkedExecutions) {
      setIsShared(linkedExecutions.is_shared);
      setShareToken(linkedExecutions.share_token);
    }
  }, [linkedExecutions]);

  const enableMutation = useMutation({
    mutationFn: () =>
      enableChatShareApi(sessionId, {
        linked_execution_ids: Array.from(selectedExecutionIds),
      }),
    onSuccess: (response) => {
      setIsShared(true);
      setShareToken(response.share_token);
      queryClient.invalidateQueries({ queryKey: linkedExecutionsKey });
      toast({
        title: "Chat sharing enabled",
        description:
          "Anyone with the link can now view this conversation. Revoke any time.",
      });
    },
    onError: () => {
      toast({
        title: "Failed to enable sharing",
        description: "Please try again.",
        variant: "destructive",
      });
    },
  });

  const disableMutation = useMutation({
    mutationFn: () => disableChatShareApi(sessionId),
    onSuccess: () => {
      setIsShared(false);
      setShareToken(null);
      setSelectedExecutionIds(new Set());
      queryClient.invalidateQueries({ queryKey: linkedExecutionsKey });
      toast({
        title: "Chat sharing disabled",
        description: "The share link is no longer accessible.",
      });
    },
    onError: () => {
      toast({
        title: "Failed to disable sharing",
        description: "Please try again.",
        variant: "destructive",
      });
    },
  });

  const shareUrl = shareToken ? chatShareUrl(shareToken) : "";

  function toggleExecution(executionId: string) {
    setSelectedExecutionIds((prev) => {
      const next = new Set(prev);
      if (next.has(executionId)) {
        next.delete(executionId);
      } else {
        next.add(executionId);
      }
      return next;
    });
  }

  async function copyShareUrl() {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast({
        title: "Failed to copy link",
        variant: "destructive",
      });
    }
  }

  return {
    isShared,
    shareToken,
    shareUrl,
    copied,
    linkedExecutions: linkedExecutions?.linked_executions ?? [],
    isLoadingLinks,
    selectedExecutionIds,
    toggleExecution,
    enable: () => enableMutation.mutate(),
    isEnabling: enableMutation.isPending,
    disable: () => disableMutation.mutate(),
    isDisabling: disableMutation.isPending,
    copyShareUrl,
  };
}

export type ShareChatDialogState = ReturnType<typeof useShareChatDialog>;
export type { SharedChatLinkedExecution };
