import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  getGetV2ListLinkedExecutionsQueryKey,
  useDeleteV2DisableChatSharing,
  useGetV2ListLinkedExecutions,
  usePostV2EnableChatSharing,
} from "@/app/api/__generated__/endpoints/chat/chat";
import type { SharedChatLinkedExecution } from "@/app/api/__generated__/models/sharedChatLinkedExecution";
import { useToast } from "@/components/molecules/Toast/use-toast";
import { chatShareUrl } from "@/lib/share/routes";

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

  // Only load linked executions when the modal opens — the scan can touch
  // many tool messages, so we don't run it eagerly on chat mount.
  const { data: linkedExecutionsResponse, isLoading: isLoadingLinks } =
    useGetV2ListLinkedExecutions(sessionId, {
      query: {
        enabled: open,
        select: (res) => (res.status === 200 ? res.data : undefined),
      },
    });

  // Hydrate local share state from the backend payload so the modal
  // opens in the right mode after a reload.
  useEffect(() => {
    if (linkedExecutionsResponse) {
      setIsShared(linkedExecutionsResponse.is_shared ?? false);
      setShareToken(linkedExecutionsResponse.share_token ?? null);
    }
  }, [linkedExecutionsResponse]);

  const invalidateLinks = () =>
    queryClient.invalidateQueries({
      queryKey: getGetV2ListLinkedExecutionsQueryKey(sessionId),
    });

  const { mutate: enable, isPending: isEnabling } = usePostV2EnableChatSharing({
    mutation: {
      onSuccess: (res) => {
        if (res.status !== 200) {
          toast({
            title: "Failed to enable sharing",
            description: "Please try again.",
            variant: "destructive",
          });
          return;
        }
        setIsShared(true);
        setShareToken(res.data.share_token);
        invalidateLinks();
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
    },
  });

  const { mutate: disable, isPending: isDisabling } =
    useDeleteV2DisableChatSharing({
      mutation: {
        onSuccess: () => {
          setIsShared(false);
          setShareToken(null);
          setSelectedExecutionIds(new Set());
          invalidateLinks();
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
    linkedExecutions: linkedExecutionsResponse?.linked_executions ?? [],
    isLoadingLinks,
    selectedExecutionIds,
    toggleExecution,
    enable: () =>
      enable({
        sessionId,
        data: { linked_execution_ids: Array.from(selectedExecutionIds) },
      }),
    isEnabling,
    disable: () => disable({ sessionId }),
    isDisabling,
    copyShareUrl,
  };
}

export type ShareChatDialogState = ReturnType<typeof useShareChatDialog>;
export type { SharedChatLinkedExecution };
