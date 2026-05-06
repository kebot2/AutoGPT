"use client";

import { Sparkle, X } from "@phosphor-icons/react";
import { LATEST_ENTRY } from "./manifest";
import { cn } from "@/lib/utils";

interface Props {
  onClick: () => void;
  onDismiss: () => void;
  visible: boolean;
}

export function ChangelogPill({ onClick, onDismiss, visible }: Props) {
  return (
    <div
      className={cn(
        "fixed bottom-6 left-6 z-40 transition-all duration-500",
        visible
          ? "pointer-events-auto translate-y-0 opacity-100"
          : "pointer-events-none translate-y-6 opacity-0",
      )}
      style={{
        transitionTimingFunction: "cubic-bezier(0.34, 1.56, 0.64, 1)",
      }}
    >
      <button
        onClick={onClick}
        className={cn(
          "group flex w-[320px] items-center gap-3 rounded-xl py-3 pr-4 pl-3 text-left",
          "bg-background border-border/80 border",
          "hover:border-border transition-all hover:shadow-lg",
          "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
        )}
        style={{
          boxShadow:
            "0 1px 0 rgba(0,0,0,0.02), 0 4px 16px rgba(0,0,0,0.06), 0 12px 32px rgba(0,0,0,0.04)",
        }}
        aria-label={`What's new: ${LATEST_ENTRY.title}`}
      >
        <div
          className="relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg"
          style={{
            background:
              "linear-gradient(135deg, #fef3c7 0%, #fde68a 50%, #f59e0b 100%)",
          }}
        >
          <Sparkle className="h-4 w-4 text-stone-800/70" weight="fill" />
          <span
            className="absolute top-1 right-1 h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500"
            aria-hidden
          />
        </div>

        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex items-center gap-1.5">
            <span className="text-[10px] font-semibold tracking-[0.12em] text-emerald-600 uppercase">
              New
            </span>
            <span className="text-muted-foreground text-[11px]">·</span>
            <span className="text-muted-foreground font-serif text-[11px] italic">
              {LATEST_ENTRY.dateLabel.split("–")[1]?.trim() ??
                LATEST_ENTRY.dateLabel}
            </span>
          </div>
          <div className="text-foreground truncate text-[13px] leading-tight font-medium">
            {LATEST_ENTRY.title}
          </div>
        </div>

        <span
          role="button"
          tabIndex={0}
          aria-label="Dismiss"
          className="hover:bg-muted -m-1 shrink-0 cursor-pointer rounded p-1 opacity-0 transition-opacity group-hover:opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onDismiss();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.stopPropagation();
              onDismiss();
            }
          }}
        >
          <X className="text-muted-foreground h-3.5 w-3.5" />
        </span>
      </button>
    </div>
  );
}
