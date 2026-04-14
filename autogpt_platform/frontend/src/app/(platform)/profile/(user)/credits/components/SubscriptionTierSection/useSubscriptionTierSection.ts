import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  useGetSubscriptionStatus,
  useUpdateSubscriptionTier,
} from "@/app/api/__generated__/endpoints/credits/credits";
import type { SubscriptionStatusResponse } from "@/app/api/__generated__/models/subscriptionStatusResponse";
import type { SubscriptionTierRequestTier } from "@/app/api/__generated__/models/subscriptionTierRequestTier";
import { useToast } from "@/components/molecules/Toast/use-toast";

export type SubscriptionStatus = SubscriptionStatusResponse;

const TIER_ORDER = ["FREE", "PRO", "BUSINESS", "ENTERPRISE"];

export function useSubscriptionTierSection() {
  const searchParams = useSearchParams();
  const subscriptionStatus = searchParams.get("subscription");
  const router = useRouter();
  const pathname = usePathname();
  const { toast } = useToast();
  const [tierError, setTierError] = useState<string | null>(null);

  const {
    data: subscription,
    isLoading,
    error: queryError,
    refetch,
  } = useGetSubscriptionStatus({
    query: { select: (data) => (data.status === 200 ? data.data : null) },
  });

  const fetchError = queryError ? "Failed to load subscription info" : null;

  const {
    mutateAsync: doUpdateTier,
    isPending,
    variables,
  } = useUpdateSubscriptionTier();

  useEffect(() => {
    if (subscriptionStatus === "success") {
      refetch();
      toast({
        title: "Subscription upgraded",
        description:
          "Your plan has been updated. It may take a moment to reflect.",
      });
    }
    // Strip ?subscription=success|cancelled from the URL so a page refresh
    // does not re-trigger side-effects, and so a second checkout in the same
    // session correctly fires the toast again.
    if (
      subscriptionStatus === "success" ||
      subscriptionStatus === "cancelled"
    ) {
      router.replace(pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch and toast
    // are new references each render but are stable in practice; the effect must
    // only re-run when subscriptionStatus/pathname changes.
  }, [subscriptionStatus, refetch, toast, router, pathname]);

  async function changeTier(tier: string) {
    setTierError(null);
    try {
      const successUrl = `${window.location.origin}${window.location.pathname}?subscription=success`;
      const cancelUrl = `${window.location.origin}${window.location.pathname}?subscription=cancelled`;
      const result = await doUpdateTier({
        data: {
          tier: tier as SubscriptionTierRequestTier,
          success_url: successUrl,
          cancel_url: cancelUrl,
        },
      });
      if (result.status === 200 && result.data.url) {
        window.location.href = result.data.url;
        return;
      }
      await refetch();
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : "Failed to change subscription tier";
      setTierError(msg);
    }
  }

  function handleTierChange(
    targetTierKey: string,
    currentTier: string,
    onConfirmDowngrade: (tier: string) => void,
  ) {
    const currentIdx = TIER_ORDER.indexOf(currentTier);
    const targetIdx = TIER_ORDER.indexOf(targetTierKey);
    if (targetIdx < currentIdx) {
      onConfirmDowngrade(targetTierKey);
      return;
    }
    void changeTier(targetTierKey);
  }

  const pendingTier =
    isPending && variables?.data?.tier ? variables.data.tier : null;

  return {
    subscription: subscription ?? null,
    isLoading,
    error: fetchError,
    tierError,
    isPending,
    pendingTier,
    changeTier,
    handleTierChange,
  };
}
