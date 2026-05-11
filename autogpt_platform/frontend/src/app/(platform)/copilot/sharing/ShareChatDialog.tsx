"use client";

import { CheckIcon, CopyIcon, ShareNetworkIcon } from "@phosphor-icons/react";
import { Button } from "@/components/atoms/Button/Button";
import { Switch } from "@/components/atoms/Switch/Switch";
import { Text } from "@/components/atoms/Text/Text";
import { Dialog } from "@/components/molecules/Dialog/Dialog";
import { useShareChatDialog } from "./useShareChatDialog";

type Props = {
  sessionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function ShareChatDialog({ sessionId, open, onOpenChange }: Props) {
  const state = useShareChatDialog({ sessionId, open });

  return (
    <Dialog
      title="Share this chat"
      controlled={{ isOpen: open, set: onOpenChange }}
    >
      <Dialog.Content>
        <div className="space-y-4">
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
            <Text variant="small" className="text-amber-900">
              Anyone with the link will see this conversation. Don&apos;t share
              if it contains secrets you pasted, personal details, or
              credentials you wouldn&apos;t want public.
            </Text>
          </div>

          {state.linkedExecutions.length > 0 && (
            <div className="space-y-2">
              <Text variant="small" className="font-medium">
                This chat ran {state.linkedExecutions.length} agent
                {state.linkedExecutions.length === 1 ? "" : "s"}. Choose which
                to include in the share:
              </Text>
              <ul className="space-y-2">
                {state.linkedExecutions.map((execution) => {
                  const checked = state.selectedExecutionIds.has(
                    execution.execution_id,
                  );
                  const alreadyShared = !!execution.share_token;
                  return (
                    <li
                      key={execution.execution_id}
                      className="flex items-center justify-between rounded border border-zinc-200 px-3 py-2"
                    >
                      <div className="flex flex-col">
                        <Text variant="body">
                          {execution.graph_name || "Untitled agent"}
                        </Text>
                        {alreadyShared && (
                          <Text variant="small" className="text-zinc-500">
                            Already shared independently
                          </Text>
                        )}
                      </div>
                      <Switch
                        checked={checked}
                        onCheckedChange={() =>
                          state.toggleExecution(execution.execution_id)
                        }
                        disabled={state.isShared}
                        aria-label={`Include ${execution.graph_name || "agent"} in share`}
                      />
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {state.isShared && state.shareUrl && (
            <div className="space-y-2">
              <Text variant="small" className="font-medium">
                Share link
              </Text>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={state.shareUrl}
                  className="flex-1 rounded border border-zinc-200 bg-zinc-50 px-2 py-1.5 font-mono text-xs"
                />
                <Button
                  size="small"
                  variant="secondary"
                  onClick={state.copyShareUrl}
                  leftIcon={
                    state.copied ? (
                      <CheckIcon size={14} weight="bold" />
                    ) : (
                      <CopyIcon size={14} />
                    )
                  }
                >
                  {state.copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          )}
        </div>
        <Dialog.Footer>
          {state.isShared ? (
            <Button
              variant="destructive"
              onClick={state.disable}
              loading={state.isDisabling}
            >
              Stop sharing
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={state.enable}
              loading={state.isEnabling}
              disabled={state.isLoadingLinks}
              leftIcon={<ShareNetworkIcon size={14} />}
            >
              Enable sharing
            </Button>
          )}
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog>
  );
}
