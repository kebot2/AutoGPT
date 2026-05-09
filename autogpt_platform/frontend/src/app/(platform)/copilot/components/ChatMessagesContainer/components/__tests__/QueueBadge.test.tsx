import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { render } from "@/tests/integrations/test-utils";
import { QueueBadge } from "../QueueBadge";

const cancelMock = vi.fn();
let isPending = false;

vi.mock("@/app/api/__generated__/endpoints/chat/chat", () => ({
  useDeleteV2CancelQueuedTask: ({
    mutation,
  }: {
    mutation?: {
      onSuccess?: (response: { status: number }) => void;
      onError?: (error: unknown) => void;
    };
  }) => ({
    mutate: (args: { messageId: string }) => {
      cancelMock(args);
      mutation?.onSuccess?.({ status: 204 });
    },
    isPending,
  }),
  getGetV2GetSessionQueryKey: (sessionId: string) => [
    `/api/chat/sessions/${sessionId}`,
  ],
}));

vi.mock("@sentry/nextjs", () => ({ captureException: vi.fn() }));
vi.mock("@/components/molecules/Toast/use-toast", () => ({ toast: vi.fn() }));

afterEach(() => {
  cleanup();
  cancelMock.mockClear();
  isPending = false;
});

describe("QueueBadge — queued", () => {
  it("renders the queued badge with the cancel button when a raw id is present", () => {
    render(
      <QueueBadge
        queueStatus="queued"
        rawMessageId="msg-1"
        sessionID="sess-1"
      />,
    );
    expect(screen.getByTestId("queue-badge-queued")).toBeDefined();
    expect(screen.getByTestId("queue-cancel-button")).toBeDefined();
  });

  it("invokes the cancel mutation with the raw message id on click", () => {
    render(
      <QueueBadge
        queueStatus="queued"
        rawMessageId="msg-42"
        sessionID="sess-1"
      />,
    );
    fireEvent.click(screen.getByTestId("queue-cancel-button"));
    expect(cancelMock).toHaveBeenCalledWith({ messageId: "msg-42" });
  });

  it("hides the cancel button when no raw id is available", () => {
    render(<QueueBadge queueStatus="queued" sessionID="sess-1" />);
    expect(screen.getByTestId("queue-badge-queued")).toBeDefined();
    expect(screen.queryByTestId("queue-cancel-button")).toBeNull();
  });
});

describe("QueueBadge — blocked", () => {
  it("renders the blocked badge with no cancel button", () => {
    render(
      <QueueBadge
        queueStatus="blocked"
        queueBlockedReason="Subscription required"
        sessionID="sess-1"
      />,
    );
    expect(screen.getByTestId("queue-badge-blocked")).toBeDefined();
    expect(screen.queryByTestId("queue-cancel-button")).toBeNull();
  });
});
