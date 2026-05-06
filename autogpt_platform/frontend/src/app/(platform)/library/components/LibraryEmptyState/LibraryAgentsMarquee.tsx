"use client";

import { FileIcon } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";

const TILE_COUNT = 6;
const ROW_A = Array.from({ length: TILE_COUNT });
const ROW_B = Array.from({ length: TILE_COUNT });

export function LibraryAgentsMarquee() {
  const reduceMotion = useReducedMotion();

  return (
    <div
      aria-hidden
      className="relative flex h-[140px] w-full flex-col justify-center gap-3 overflow-hidden"
      style={{
        maskImage:
          "linear-gradient(to right, transparent 0%, black 18%, black 82%, transparent 100%)",
        WebkitMaskImage:
          "linear-gradient(to right, transparent 0%, black 18%, black 82%, transparent 100%)",
      }}
    >
      <MarqueeRow
        tiles={ROW_A}
        direction="left"
        reduceMotion={!!reduceMotion}
      />
      <MarqueeRow
        tiles={ROW_B}
        direction="right"
        reduceMotion={!!reduceMotion}
      />
    </div>
  );
}

type RowProps = {
  tiles: unknown[];
  direction: "left" | "right";
  reduceMotion: boolean;
};

function MarqueeRow({ tiles, direction, reduceMotion }: RowProps) {
  const animateX = direction === "left" ? ["0%", "-50%"] : ["-50%", "0%"];

  return (
    <motion.div
      className="flex w-max gap-3 will-change-transform"
      animate={reduceMotion ? undefined : { x: animateX }}
      transition={
        reduceMotion
          ? undefined
          : { duration: 22, ease: "linear", repeat: Infinity }
      }
    >
      {[...tiles, ...tiles].map((_, i) => (
        <GhostAgentCard key={i} />
      ))}
    </motion.div>
  );
}

function GhostAgentCard() {
  return (
    <div className="flex h-[56px] w-[176px] shrink-0 items-center gap-3 rounded-xl border border-zinc-200/50 bg-white/50 px-3 opacity-70 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-zinc-100">
        <FileIcon className="size-4 text-zinc-500" weight="fill" />
      </div>
      <div className="flex flex-1 flex-col gap-1.5">
        <div className="h-2 w-3/4 rounded-full bg-zinc-100" />
        <div className="h-2 w-1/2 rounded-full bg-zinc-100" />
      </div>
    </div>
  );
}
