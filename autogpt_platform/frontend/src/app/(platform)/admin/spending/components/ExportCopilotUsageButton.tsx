"use client";

import { useState } from "react";
import { ChartLineIcon } from "@phosphor-icons/react";
import { Button } from "@/components/atoms/Button/Button";
import { Dialog } from "@/components/molecules/Dialog/Dialog";
import { useToast } from "@/components/molecules/Toast/use-toast";
import { getV2ExportCopilotWeeklyUsageVsRateLimit } from "@/app/api/__generated__/endpoints/admin/admin";
import { okData } from "@/app/api/helpers";
import {
  buildCopilotUsageCsv,
  dateInputToUtcIso,
  dateInputToUtcIsoEnd,
  defaultEndDate,
  defaultStartDate,
  downloadCsv,
} from "../helpers";

export function ExportCopilotUsageButton() {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState(defaultStartDate);
  const [end, setEnd] = useState(defaultEndDate);
  const [exporting, setExporting] = useState(false);

  async function handleExport() {
    if (!start || !end) {
      toast({
        title: "Pick a date range",
        description: "Both start and end dates are required.",
        variant: "destructive",
      });
      return;
    }
    setExporting(true);
    try {
      const response = await getV2ExportCopilotWeeklyUsageVsRateLimit({
        start: dateInputToUtcIso(start) as unknown as Date,
        end: dateInputToUtcIsoEnd(end) as unknown as Date,
      });
      const data = okData(response);
      if (!data) {
        const status = (response as { status?: number }).status;
        const detail =
          (response as { data?: { detail?: string } }).data?.detail ??
          "Export failed";
        toast({
          title: status === 400 ? "Window too large" : "Export failed",
          description: detail,
          variant: "destructive",
        });
        return;
      }
      const csv = buildCopilotUsageCsv(data.rows);
      downloadCsv(csv, `copilot_weekly_usage_${start}_${end}.csv`);
      toast({
        title: "Export ready",
        description: `${data.total_rows} (user, week) rows downloaded.`,
      });
      setOpen(false);
    } finally {
      setExporting(false);
    }
  }

  return (
    <Dialog
      title="Export copilot weekly usage"
      styling={{ maxWidth: "30rem" }}
      controlled={{ isOpen: open, set: setOpen }}
    >
      <Dialog.Trigger>
        <Button
          variant="secondary"
          size="small"
          leftIcon={<ChartLineIcon weight="bold" />}
        >
          Copilot Usage CSV
        </Button>
      </Dialog.Trigger>
      <Dialog.Content>
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            Aggregates copilot:* spend by user and ISO week and joins each row
            against the user&apos;s tier-derived weekly limit.
          </p>
          <div className="flex gap-3">
            <div className="flex flex-1 flex-col gap-1">
              <label htmlFor="copilot-export-start" className="text-sm">
                Start date (UTC)
              </label>
              <input
                id="copilot-export-start"
                type="date"
                className="rounded border px-3 py-1.5 text-sm"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
            </div>
            <div className="flex flex-1 flex-col gap-1">
              <label htmlFor="copilot-export-end" className="text-sm">
                End date (UTC)
              </label>
              <input
                id="copilot-export-end"
                type="date"
                className="rounded border px-3 py-1.5 text-sm"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Window is capped at 90 days and 100k rows.
          </p>
        </div>
        <Dialog.Footer>
          <Button
            variant="secondary"
            size="small"
            onClick={() => setOpen(false)}
            disabled={exporting}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            size="small"
            onClick={handleExport}
            loading={exporting}
          >
            Download CSV
          </Button>
        </Dialog.Footer>
      </Dialog.Content>
    </Dialog>
  );
}
