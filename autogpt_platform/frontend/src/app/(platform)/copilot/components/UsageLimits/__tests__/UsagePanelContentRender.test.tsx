import {
  render,
  screen,
  cleanup,
  fireEvent,
} from "@/tests/integrations/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UsagePanelContent, formatBytes } from "../UsagePanelContent";
import type { CoPilotUsagePublic } from "@/app/api/__generated__/models/coPilotUsagePublic";

const mockResetUsage = vi.fn();
vi.mock("../../../hooks/useResetRateLimit", () => ({
  useResetRateLimit: () => ({ resetUsage: mockResetUsage, isPending: false }),
}));

const mockStorageData = vi.fn();
vi.mock("../useWorkspaceStorage", () => ({
  useWorkspaceStorage: () => mockStorageData(),
}));

afterEach(() => {
  cleanup();
  mockResetUsage.mockReset();
  mockStorageData.mockReset();
});

// Default: no storage data (most existing tests don't need it)
beforeEach(() => {
  mockStorageData.mockReturnValue({ data: undefined });
});

function makeUsage(
  overrides: Partial<{
    dailyPercent: number | null;
    weeklyPercent: number | null;
    tier: string;
    resetCost: number;
  }> = {},
): CoPilotUsagePublic {
  const {
    dailyPercent = 5,
    weeklyPercent = 4,
    tier = "BASIC",
    resetCost = 100,
  } = overrides;
  const future = new Date(Date.now() + 3600 * 1000).toISOString();
  return {
    daily:
      dailyPercent === null
        ? null
        : { percent_used: dailyPercent, resets_at: future },
    weekly:
      weeklyPercent === null
        ? null
        : { percent_used: weeklyPercent, resets_at: future },
    tier,
    reset_cost: resetCost,
  } as CoPilotUsagePublic;
}

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1024, "1 KB"],
    [250 * 1024, "250 KB"],
    [1023 * 1024, "1023 KB"],
    [1000 * 1024, "1000 KB"],
    [1024 * 1024, "1 MB"],
    [250 * 1024 * 1024, "250 MB"],
    [1000 * 1024 * 1024, "1000 MB"],
    [1024 * 1024 * 1024, "1.0 GB"],
    [5 * 1024 * 1024 * 1024, "5.0 GB"],
    [15 * 1024 * 1024 * 1024, "15.0 GB"],
  ])("formats %d bytes as %s", (input, expected) => {
    expect(formatBytes(input)).toBe(expected);
  });

  // Adversarial edge cases
  it("handles 1 byte", () => {
    expect(formatBytes(1)).toBe("1 B");
  });

  it("handles exactly 1023 bytes (just under 1 KB)", () => {
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("auto-promotes 1 MB - 1 byte to MB (rounds up to 1024 KB → 1.0 MB)", () => {
    // 1048575 / 1024 = 1023.999 → Math.round = 1024 → kb >= 1024 → promotes to MB
    expect(formatBytes(1048575)).toBe("1.0 MB");
  });

  it("auto-promotes 1 GB - 1 byte to GB (rounds up to 1024 MB → 1.0 GB)", () => {
    // 1073741823 / (1024*1024) = 1023.999 → Math.round = 1024 → promotes to GB
    expect(formatBytes(1073741823)).toBe("1.0 GB");
  });

  it("handles very large values (1 TB)", () => {
    expect(formatBytes(1024 * 1024 * 1024 * 1024)).toBe("1024.0 GB");
  });
});

describe("UsagePanelContent", () => {
  it("renders 'No usage limits configured' when both windows are null", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: null, weeklyPercent: null })}
      />,
    );
    expect(screen.getByText("No usage limits configured")).toBeDefined();
  });

  it("still renders file storage when usage windows are null", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 100 * 1024 * 1024,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 40,
        file_count: 5,
      },
    });

    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: null, weeklyPercent: null })}
      />,
    );

    expect(screen.getByText("No usage limits configured")).toBeDefined();
    expect(screen.getByText("File storage")).toBeDefined();
  });

  it("renders the reset button when daily limit is exhausted", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: 100, resetCost: 50 })}
      />,
    );
    expect(screen.getByText(/Reset daily limit/)).toBeDefined();
  });

  it("does not render the reset button when weekly limit is also exhausted", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({
          dailyPercent: 100,
          weeklyPercent: 100,
          resetCost: 50,
        })}
      />,
    );
    expect(screen.queryByText(/Reset daily limit/)).toBeNull();
  });

  it("calls resetUsage when the reset button is clicked", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: 100, resetCost: 50 })}
      />,
    );
    fireEvent.click(screen.getByText(/Reset daily limit/));
    expect(mockResetUsage).toHaveBeenCalled();
  });

  it("renders 'Add credits' link when insufficient credits", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: 100, resetCost: 50 })}
        hasInsufficientCredits={true}
        isBillingEnabled={true}
      />,
    );
    expect(screen.getByText("Add credits to reset")).toBeDefined();
  });

  it("renders percent used in the usage bar", () => {
    render(<UsagePanelContent usage={makeUsage({ dailyPercent: 25 })} />);
    expect(screen.getByText("25% used")).toBeDefined();
  });

  it("renders '<1% used' when usage is greater than 0 but rounds to 0", () => {
    render(<UsagePanelContent usage={makeUsage({ dailyPercent: 0.3 })} />);
    expect(screen.getByText("<1% used")).toBeDefined();
  });

  it("renders file storage bar when workspace data is available", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 100 * 1024 * 1024,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 40,
        file_count: 5,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText("File storage")).toBeDefined();
    expect(screen.getByText(/100 MB of 250 MB/)).toBeDefined();
    expect(screen.getByText(/5 files/)).toBeDefined();
  });

  it("hides file storage bar when no workspace data", () => {
    mockStorageData.mockReturnValue({ data: undefined });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.queryByText("File storage")).toBeNull();
  });

  it("hides file storage bar when limit is zero", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 0,
        limit_bytes: 0,
        used_percent: 0,
        file_count: 0,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.queryByText("File storage")).toBeNull();
  });

  it("shows orange bar when storage usage is at or above 80%", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 210 * 1024 * 1024,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 84,
        file_count: 3,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText("File storage")).toBeDefined();
    expect(screen.getByText("84% used")).toBeDefined();
  });

  it("shows singular 'file' for single file", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 1024,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 0,
        file_count: 1,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText(/1 file$/)).toBeDefined();
  });

  it("shows storage '<1% used' when usage is tiny", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 100,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 0.001,
        file_count: 1,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText("File storage")).toBeDefined();
  });

  it("renders header with tier label", () => {
    render(<UsagePanelContent usage={makeUsage({ tier: "PRO" })} />);
    expect(screen.getByText("Pro plan")).toBeDefined();
  });

  it("hides header when showHeader is false", () => {
    render(<UsagePanelContent usage={makeUsage()} showHeader={false} />);
    expect(screen.queryByText("Usage limits")).toBeNull();
  });

  // Adversarial edge cases

  it("hides storage bar when limit is negative", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 100,
        limit_bytes: -1,
        used_percent: 0,
        file_count: 1,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.queryByText("File storage")).toBeNull();
  });

  it("handles storage at exactly 100% used", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 250 * 1024 * 1024,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 100,
        file_count: 10,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText("100% used")).toBeDefined();
    expect(screen.getByText(/250 MB of 250 MB/)).toBeDefined();
  });

  it("clamps storage above 100% to 100% display", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 300 * 1024 * 1024,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 120,
        file_count: 15,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    // Should show "100% used", not "120% used"
    expect(screen.getByText("100% used")).toBeDefined();
    expect(screen.getByText("File storage")).toBeDefined();
  });

  it("handles zero files with zero usage", () => {
    mockStorageData.mockReturnValue({
      data: {
        used_bytes: 0,
        limit_bytes: 250 * 1024 * 1024,
        used_percent: 0,
        file_count: 0,
      },
    });

    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText("File storage")).toBeDefined();
    expect(screen.getByText("0% used")).toBeDefined();
    expect(screen.getByText(/0 files/)).toBeDefined();
  });

  it("renders billing link by default", () => {
    render(<UsagePanelContent usage={makeUsage()} />);
    expect(screen.getByText("Learn more about usage limits")).toBeDefined();
  });

  it("hides billing link when showBillingLink is false", () => {
    render(<UsagePanelContent usage={makeUsage()} showBillingLink={false} />);
    expect(screen.queryByText("Learn more about usage limits")).toBeNull();
  });

  it("renders only daily bar when weekly is null", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: 50, weeklyPercent: null })}
      />,
    );
    expect(screen.getByText("Today")).toBeDefined();
    expect(screen.queryByText("This week")).toBeNull();
  });

  it("renders only weekly bar when daily is null", () => {
    render(
      <UsagePanelContent
        usage={makeUsage({ dailyPercent: null, weeklyPercent: 30 })}
      />,
    );
    expect(screen.queryByText("Today")).toBeNull();
    expect(screen.getByText("This week")).toBeDefined();
  });

  it("does not show tier label when tier is missing", () => {
    const usage = makeUsage();
    (usage as Record<string, unknown>).tier = null;
    render(<UsagePanelContent usage={usage} />);
    expect(screen.queryByText(/plan$/)).toBeNull();
    expect(screen.getByText("Usage limits")).toBeDefined();
  });
});
