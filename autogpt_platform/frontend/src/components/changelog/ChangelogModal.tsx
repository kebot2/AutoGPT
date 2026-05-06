"use client";

import { useEffect, useRef } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { CHANGELOG_MANIFEST } from "./manifest";
import { ChangelogContent } from "./ChangelogContent";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  activeID: string;
  onActiveIDChange: (id: string) => void;
}

export function ChangelogModal({
  open,
  onOpenChange,
  activeID,
  onActiveIDChange,
}: Props) {
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = 0;
  }, [activeID]);

  const activeEntry =
    CHANGELOG_MANIFEST.find((e) => e.id === activeID) ?? CHANGELOG_MANIFEST[0];

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-black/60" />
        <DialogPrimitive.Content
          className={cn(
            "fixed top-[50%] left-[50%] z-50 translate-x-[-50%] translate-y-[-50%]",
            "gap-0 overflow-hidden p-0",
            "h-[78vh] max-h-[820px] w-[92vw] max-w-[1080px]",
            "flex flex-row",
            "bg-background border-border rounded-lg border shadow-xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            "data-[state=closed]:slide-out-to-left-1/2 data-[state=closed]:slide-out-to-top-[48%]",
            "data-[state=open]:slide-in-from-left-1/2 data-[state=open]:slide-in-from-top-[48%]",
          )}
        >
          <span className="sr-only">
            <DialogPrimitive.Title>
              What&apos;s new in AutoGPT
            </DialogPrimitive.Title>
          </span>

          {/* Sidebar */}
          <aside className="border-border bg-muted/30 flex w-[280px] shrink-0 flex-col border-r">
            <div className="px-6 pt-6 pb-4">
              <div className="mb-1 flex items-center gap-2">
                <div
                  className="h-2 w-2 rounded-full"
                  style={{
                    background: "linear-gradient(135deg, #f59e0b, #ef4444)",
                  }}
                />
                <span className="text-muted-foreground text-[11px] font-semibold tracking-[0.18em] uppercase">
                  AutoGPT
                </span>
              </div>
              <h2
                className="text-[22px] leading-tight"
                style={{
                  fontFamily:
                    "var(--font-changelog-display, ui-serif, Georgia, serif)",
                }}
              >
                What&apos;s new
              </h2>
            </div>

            <nav
              className="flex-1 overflow-y-auto px-3 pb-3"
              aria-label="Changelog entries"
            >
              {CHANGELOG_MANIFEST.map((entry) => {
                const isActive = entry.id === activeID;
                return (
                  <button
                    key={entry.id}
                    onClick={() => onActiveIDChange(entry.id)}
                    className={cn(
                      "group relative mb-0.5 w-full rounded-lg border px-3 py-2.5 text-left transition-all",
                      isActive
                        ? "bg-background border-border/80 shadow-sm"
                        : "hover:bg-background/60 border-transparent",
                    )}
                    aria-current={isActive ? "page" : undefined}
                  >
                    {isActive && entry.isHighlighted && (
                      <span
                        className="absolute top-2 bottom-2 left-0 w-[2px] rounded-full"
                        style={{
                          background:
                            "linear-gradient(to bottom, #f59e0b, #ef4444)",
                        }}
                        aria-hidden
                      />
                    )}
                    <div className="mb-1 flex items-center gap-1.5">
                      <span className="text-muted-foreground font-serif text-[10px] italic">
                        {entry.dateLabel}
                      </span>
                      {entry.isHighlighted && (
                        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold tracking-wider text-emerald-700 uppercase">
                          New
                        </span>
                      )}
                    </div>
                    <div
                      className={cn(
                        "line-clamp-2 text-[13px] leading-snug transition-colors",
                        isActive
                          ? "text-foreground font-medium"
                          : "text-muted-foreground group-hover:text-foreground",
                      )}
                    >
                      {entry.title}
                    </div>
                  </button>
                );
              })}
            </nav>

            <div className="border-border text-muted-foreground border-t px-6 py-4 text-[11px]">
              Press{" "}
              <kbd className="bg-muted text-foreground/80 rounded px-1.5 py-0.5 font-mono text-[10px]">
                esc
              </kbd>{" "}
              to close
            </div>
          </aside>

          {/* Content */}
          <main className="relative flex-1">
            <div
              ref={contentRef}
              className="absolute inset-0 overflow-y-auto px-14 py-12"
            >
              <div className="mx-auto max-w-[640px]">
                <ChangelogContent entry={activeEntry} />
              </div>
            </div>
          </main>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
