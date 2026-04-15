import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@/tests/integrations/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DecomposeGoalTool } from "../DecomposeGoal";
import type { TaskDecompositionOutput } from "../helpers";

const mockOnSend = vi.fn();
vi.mock(
  "../../../components/CopilotChatActionsProvider/useCopilotChatActions",
  () => ({
    useCopilotChatActions: () => ({ onSend: mockOnSend }),
  }),
);

vi.mock("@/app/api/__generated__/endpoints/chat/chat", () => ({
  postV2CancelAutoApproveTask: vi.fn(() => Promise.resolve()),
}));

const STEPS = [
  {
    step_id: "step_1",
    description: "Add input block",
    action: "add_input",
    block_name: null,
    status: "pending",
  },
  {
    step_id: "step_2",
    description: "Add AI summarizer",
    action: "add_block",
    block_name: "AI Text Generator",
    status: "pending",
  },
  {
    step_id: "step_3",
    description: "Connect blocks",
    action: "connect_blocks",
    block_name: null,
    status: "pending",
  },
];

const DECOMPOSITION: TaskDecompositionOutput = {
  type: "task_decomposition",
  message: "Here's the plan (3 steps):",
  goal: "Build a news summarizer",
  steps: STEPS,
  step_count: 3,
  requires_approval: true,
  auto_approve_seconds: 60,
  created_at: new Date().toISOString(),
  session_id: "test-session-1",
};

function makePart(
  state: string,
  output?: unknown,
): {
  type: string;
  toolCallId: string;
  toolName: string;
  state: string;
  input?: unknown;
  output?: unknown;
} {
  return {
    type: "tool-decompose_goal",
    toolCallId: "call_1",
    toolName: "decompose_goal",
    state,
    output,
  };
}

describe("DecomposeGoalTool", () => {
  afterEach(() => {
    cleanup();
    mockOnSend.mockClear();
  });

  it("renders analyzing animation during input-streaming", () => {
    render(
      <DecomposeGoalTool
        part={makePart("input-streaming") as any}
        isLastMessage
      />,
    );
    expect(screen.getByText(/A/)).toBeDefined();
  });

  it("renders error card when state is output-error", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-error") as any}
        isLastMessage
      />,
    );
    expect(screen.getByText(/Failed to analyze the goal/i)).toBeDefined();
    expect(screen.getByText("Try again")).toBeDefined();
  });

  it("sends retry message when Try again is clicked on error", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-error") as any}
        isLastMessage
      />,
    );
    fireEvent.click(screen.getByText("Try again"));
    expect(mockOnSend).toHaveBeenCalledWith(
      "Please try decomposing the goal again.",
    );
  });

  it("renders error card for error output object", () => {
    const errorOutput = {
      type: "error",
      error: "missing_steps",
      message: "Please provide at least one step.",
    };
    render(
      <DecomposeGoalTool
        part={makePart("output-available", errorOutput) as any}
        isLastMessage
      />,
    );
    expect(screen.getByText("Please provide at least one step.")).toBeDefined();
  });

  it("renders the build plan accordion with steps", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );
    expect(screen.getByText(/Build Plan — 3 steps/)).toBeDefined();
    expect(screen.getByText("Build a news summarizer")).toBeDefined();
    expect(screen.getByText(/Here's the plan/)).toBeDefined();
    expect(screen.getByText(/1\. Add input block/)).toBeDefined();
    expect(screen.getByText(/2\. Add AI summarizer/)).toBeDefined();
    expect(screen.getByText(/3\. Connect blocks/)).toBeDefined();
  });

  it("renders block name badges for steps that have them", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );
    expect(screen.getByText("AI Text Generator")).toBeDefined();
  });

  it("shows approve and modify buttons when requires_approval and isLastMessage", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );
    expect(screen.getByText("Modify")).toBeDefined();
    expect(screen.getByText(/Starting in/)).toBeDefined();
  });

  it("hides action buttons when isLastMessage is false", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage={false}
      />,
    );
    expect(screen.queryByText("Modify")).toBeNull();
    expect(screen.getByText(/Review the plan above and approve/)).toBeDefined();
  });

  it("hides action buttons when requires_approval is false", () => {
    const noApproval = { ...DECOMPOSITION, requires_approval: false };
    render(
      <DecomposeGoalTool
        part={makePart("output-available", noApproval) as any}
        isLastMessage
      />,
    );
    expect(screen.queryByText("Modify")).toBeNull();
  });

  it("disables buttons while message is still streaming", () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
        isMessageStreaming
      />,
    );
    const modifyBtn = screen.getByText("Modify").closest("button");
    expect(modifyBtn?.disabled).toBe(true);
  });

  it("sends approval message when approve button is clicked", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    const startBtn = screen.getByText(/Starting in/).closest("button");
    expect(startBtn).toBeDefined();
    fireEvent.click(startBtn!);

    await waitFor(() => {
      expect(mockOnSend).toHaveBeenCalledWith(
        "Approved. Please build the agent.",
      );
    });
  });

  it("does not send duplicate approval on second click", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    const startBtn = screen.getByText(/Starting in/).closest("button");
    fireEvent.click(startBtn!);
    fireEvent.click(startBtn!);

    await waitFor(() => {
      expect(mockOnSend).toHaveBeenCalledTimes(1);
    });
  });

  it("enters edit mode when Modify is clicked", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    fireEvent.click(screen.getByText("Modify"));

    await waitFor(() => {
      const textareas = screen.getAllByPlaceholderText("Step description");
      expect(textareas.length).toBe(3);
    });
  });

  it("cancels auto-approve on the server when Modify is clicked", async () => {
    const { postV2CancelAutoApproveTask } = await import(
      "@/app/api/__generated__/endpoints/chat/chat"
    );

    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    fireEvent.click(screen.getByText("Modify"));

    await waitFor(() => {
      expect(postV2CancelAutoApproveTask).toHaveBeenCalledWith(
        "test-session-1",
      );
    });
  });

  it("allows editing step descriptions in edit mode", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    fireEvent.click(screen.getByText("Modify"));

    await waitFor(() => {
      expect(screen.getAllByPlaceholderText("Step description").length).toBe(3);
    });

    const textareas = screen.getAllByPlaceholderText("Step description");
    fireEvent.change(textareas[0], {
      target: { value: "Fetch RSS feed" },
    });

    expect(
      (
        screen.getAllByPlaceholderText(
          "Step description",
        )[0] as HTMLTextAreaElement
      ).value,
    ).toBe("Fetch RSS feed");
  });

  it("allows deleting steps in edit mode", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    fireEvent.click(screen.getByText("Modify"));

    await waitFor(() => {
      expect(screen.getAllByPlaceholderText("Step description").length).toBe(3);
    });

    const removeButtons = screen.getAllByLabelText("Remove step");
    fireEvent.click(removeButtons[0]);

    await waitFor(() => {
      expect(screen.getAllByPlaceholderText("Step description").length).toBe(2);
    });
  });

  it("allows inserting new steps in edit mode", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    fireEvent.click(screen.getByText("Modify"));

    await waitFor(() => {
      expect(screen.getAllByPlaceholderText("Step description").length).toBe(3);
    });

    const insertButtons = screen.getAllByLabelText("Insert step here");
    fireEvent.click(insertButtons[0]);

    await waitFor(() => {
      expect(screen.getAllByPlaceholderText("Step description").length).toBe(4);
    });
  });

  it("sends modified steps message when approve is clicked in edit mode", async () => {
    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    fireEvent.click(screen.getByText("Modify"));

    await waitFor(() => {
      expect(screen.getAllByPlaceholderText("Step description").length).toBe(3);
    });

    const textareas = screen.getAllByPlaceholderText("Step description");
    fireEvent.change(textareas[0], {
      target: { value: "Fetch RSS feed" },
    });

    fireEvent.click(screen.getByText("Approve"));

    await waitFor(() => {
      expect(mockOnSend).toHaveBeenCalledWith(
        expect.stringContaining("Approved with modifications"),
      );
      expect(mockOnSend).toHaveBeenCalledWith(
        expect.stringContaining("Fetch RSS feed"),
      );
    });
  });

  it("renders countdown timer in the approve button", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(DECOMPOSITION.created_at!));

    render(
      <DecomposeGoalTool
        part={makePart("output-available", DECOMPOSITION) as any}
        isLastMessage
      />,
    );

    expect(screen.getByText("60")).toBeDefined();
    vi.useRealTimers();
  });

  it("renders nothing pending when output is not yet available", () => {
    const { container } = render(
      <DecomposeGoalTool
        part={makePart("input-available") as any}
        isLastMessage
      />,
    );
    expect(container.querySelector(".py-2")).toBeDefined();
  });
});
