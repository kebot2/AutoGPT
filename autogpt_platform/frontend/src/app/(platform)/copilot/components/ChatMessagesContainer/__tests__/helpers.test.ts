import { describe, expect, it } from "vitest";
import {
  buildRenderSegments,
  isCompletedToolPart,
  isInteractiveToolPart,
  parseSpecialMarkers,
  splitReasoningAndResponse,
} from "../helpers";
import type { MessagePart, RenderSegment } from "../helpers";

function textPart(text: string): MessagePart {
  return { type: "text", text } as MessagePart;
}

function toolPart(
  toolName: string,
  state: string,
  output?: unknown,
): MessagePart {
  return {
    type: `tool-${toolName}`,
    toolCallId: `call_${toolName}`,
    toolName,
    state,
    output,
  } as unknown as MessagePart;
}

describe("isCompletedToolPart", () => {
  it("returns true for output-available tool part", () => {
    const part = toolPart("some_tool", "output-available");
    expect(isCompletedToolPart(part)).toBe(true);
  });

  it("returns true for output-error tool part", () => {
    const part = toolPart("some_tool", "output-error");
    expect(isCompletedToolPart(part)).toBe(true);
  });

  it("returns false for input-streaming tool part", () => {
    const part = toolPart("some_tool", "input-streaming");
    expect(isCompletedToolPart(part)).toBe(false);
  });

  it("returns false for text part", () => {
    const part = textPart("hello");
    expect(isCompletedToolPart(part)).toBe(false);
  });
});

describe("isInteractiveToolPart", () => {
  it("returns true for task_decomposition type", () => {
    const part = toolPart("decompose_goal", "output-available", {
      type: "task_decomposition",
      message: "Plan",
      goal: "Build agent",
      steps: [],
      step_count: 0,
      requires_approval: true,
    });
    expect(isInteractiveToolPart(part)).toBe(true);
  });

  it("returns true for setup_requirements type", () => {
    const part = toolPart("run_mcp_tool", "output-available", {
      type: "setup_requirements",
      message: "Setup needed",
    });
    expect(isInteractiveToolPart(part)).toBe(true);
  });

  it("returns true for agent_details type", () => {
    const part = toolPart("find_agent", "output-available", {
      type: "agent_details",
    });
    expect(isInteractiveToolPart(part)).toBe(true);
  });

  it("returns false for non-interactive output type", () => {
    const part = toolPart("some_tool", "output-available", {
      type: "generic_output",
    });
    expect(isInteractiveToolPart(part)).toBe(false);
  });

  it("returns false when state is not output-available", () => {
    const part = toolPart("decompose_goal", "input-streaming", {
      type: "task_decomposition",
    });
    expect(isInteractiveToolPart(part)).toBe(false);
  });

  it("returns false for non-tool parts", () => {
    const part = textPart("hello");
    expect(isInteractiveToolPart(part)).toBe(false);
  });

  it("returns false when output is null", () => {
    const part = toolPart("decompose_goal", "output-available", null);
    expect(isInteractiveToolPart(part)).toBe(false);
  });

  it("handles JSON-encoded string output", () => {
    const part = toolPart(
      "decompose_goal",
      "output-available",
      JSON.stringify({ type: "task_decomposition" }),
    );
    expect(isInteractiveToolPart(part)).toBe(true);
  });

  it("returns false for invalid JSON string output", () => {
    const part = toolPart(
      "decompose_goal",
      "output-available",
      "not valid json",
    );
    expect(isInteractiveToolPart(part)).toBe(false);
  });
});

describe("buildRenderSegments", () => {
  it("returns individual segments for custom tool types", () => {
    const parts = [
      toolPart("decompose_goal", "output-available", {
        type: "task_decomposition",
      }),
    ];
    const segments = buildRenderSegments(parts);
    expect(segments).toHaveLength(1);
    expect(segments[0].kind).toBe("part");
  });

  it("collapses consecutive generic completed tool parts", () => {
    const parts = [
      toolPart("unknown_tool_a", "output-available"),
      toolPart("unknown_tool_b", "output-available"),
    ];
    const segments = buildRenderSegments(parts);
    expect(segments).toHaveLength(1);
    expect(segments[0].kind).toBe("collapsed-group");
    if (segments[0].kind === "collapsed-group") {
      expect(segments[0].parts).toHaveLength(2);
    }
  });

  it("does not collapse custom tool types into groups", () => {
    const parts = [
      toolPart("decompose_goal", "output-available", {
        type: "task_decomposition",
      }),
      toolPart("create_agent", "output-available"),
    ];
    const segments = buildRenderSegments(parts);
    expect(segments).toHaveLength(2);
    expect(segments[0].kind).toBe("part");
    expect(segments[1].kind).toBe("part");
  });

  it("renders text parts individually", () => {
    const parts = [textPart("Hello"), textPart("World")];
    const segments = buildRenderSegments(parts);
    expect(segments).toHaveLength(2);
    expect(segments.every((s) => s.kind === "part")).toBe(true);
  });

  it("handles mixed custom tools, generic tools, and text", () => {
    const parts = [
      textPart("Plan:"),
      toolPart("decompose_goal", "output-available"),
      toolPart("generic_a", "output-available"),
      toolPart("generic_b", "output-available"),
      textPart("Done"),
    ];
    const segments = buildRenderSegments(parts);

    expect(segments[0].kind).toBe("part");
    expect(segments[1].kind).toBe("part");
    expect(segments[2].kind).toBe("collapsed-group");
    expect(segments[3].kind).toBe("part");
  });

  it("does not collapse a single generic tool part", () => {
    const parts = [toolPart("generic_a", "output-available")];
    const segments = buildRenderSegments(parts);
    expect(segments).toHaveLength(1);
    expect(segments[0].kind).toBe("part");
  });

  it("preserves baseIndex offset in part segments", () => {
    const parts = [textPart("Hello")];
    const segments = buildRenderSegments(parts, 5);
    expect(segments).toHaveLength(1);
    if (segments[0].kind === "part") {
      expect(segments[0].index).toBe(5);
    }
  });
});

describe("splitReasoningAndResponse", () => {
  it("returns all parts as response when no tools are present", () => {
    const parts = [textPart("Hello"), textPart("World")];
    const { reasoning, response } = splitReasoningAndResponse(parts);
    expect(reasoning).toHaveLength(0);
    expect(response).toHaveLength(2);
  });

  it("returns all parts as response when no text follows the last tool", () => {
    const parts = [
      textPart("Thinking..."),
      toolPart("decompose_goal", "output-available", {
        type: "task_decomposition",
      }),
    ];
    const { reasoning, response } = splitReasoningAndResponse(parts);
    expect(reasoning).toHaveLength(0);
    expect(response).toHaveLength(2);
  });

  it("splits reasoning and response when text follows the last tool", () => {
    const parts = [
      textPart("Let me plan this..."),
      toolPart("decompose_goal", "output-available", {
        type: "task_decomposition",
      }),
      textPart("Here is the plan."),
    ];
    const { reasoning, response } = splitReasoningAndResponse(parts);
    expect(reasoning).toHaveLength(1);
    expect(response).toHaveLength(2);
  });

  it("pins interactive tool parts to response section", () => {
    const interactiveTool = toolPart("decompose_goal", "output-available", {
      type: "task_decomposition",
    });
    const parts = [
      textPart("Thinking..."),
      interactiveTool,
      textPart("Here is the summary."),
    ];
    const { reasoning, response } = splitReasoningAndResponse(parts);

    expect(reasoning).toHaveLength(1);
    expect(reasoning[0]).toBe(parts[0]);

    expect(response).toHaveLength(2);
    expect(response[0]).toBe(interactiveTool);
    expect(response[1]).toBe(parts[2]);
  });

  it("keeps non-interactive tool parts in reasoning", () => {
    const genericTool = toolPart("find_block", "output-available", {
      type: "block_list",
    });
    const parts = [
      textPart("Looking for blocks..."),
      genericTool,
      textPart("Found them."),
    ];
    const { reasoning, response } = splitReasoningAndResponse(parts);
    expect(reasoning).toHaveLength(2);
    expect(reasoning[1]).toBe(genericTool);
    expect(response).toHaveLength(1);
  });
});

describe("parseSpecialMarkers", () => {
  it("returns null marker for plain text", () => {
    const result = parseSpecialMarkers("Hello world");
    expect(result.markerType).toBeNull();
    expect(result.cleanText).toBe("Hello world");
  });

  it("detects error marker", () => {
    const result = parseSpecialMarkers(
      "Some preamble [__COPILOT_ERROR_f7a1__] Something went wrong",
    );
    expect(result.markerType).toBe("error");
    expect(result.markerText).toBe("Something went wrong");
  });

  it("detects retryable error marker", () => {
    const result = parseSpecialMarkers(
      "[__COPILOT_RETRYABLE_ERROR_a9c2__] Timeout reached",
    );
    expect(result.markerType).toBe("retryable_error");
    expect(result.markerText).toBe("Timeout reached");
  });

  it("detects system marker", () => {
    const result = parseSpecialMarkers(
      "[__COPILOT_SYSTEM_e3b0__] Session expired",
    );
    expect(result.markerType).toBe("system");
    expect(result.markerText).toBe("Session expired");
  });

  it("retryable takes precedence over regular error when both present", () => {
    const text =
      "[__COPILOT_RETRYABLE_ERROR_a9c2__] Retryable issue [__COPILOT_ERROR_f7a1__] Also error";
    const result = parseSpecialMarkers(text);
    expect(result.markerType).toBe("retryable_error");
  });

  it("strips marker from cleanText", () => {
    const result = parseSpecialMarkers(
      "Preamble text [__COPILOT_SYSTEM_e3b0__] System message",
    );
    expect(result.cleanText).toBe("Preamble text");
  });
});
