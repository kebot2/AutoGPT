"use client";

import { Button } from "@/components/atoms/Button/Button";
import { Text } from "@/components/atoms/Text/Text";
import {
  CodeIcon,
  SparkleIcon,
  StorefrontIcon,
} from "@phosphor-icons/react";
import { motion, useReducedMotion } from "framer-motion";
import { LibraryAgentsMarquee } from "./LibraryAgentsMarquee";

const EASE_OUT_QUINT = [0.22, 1, 0.36, 1] as const;

export function LibraryEmptyState() {
  const shouldReduceMotion = useReducedMotion();

  function fadeUp(delay: number) {
    if (shouldReduceMotion) {
      return {
        initial: { opacity: 0 },
        animate: { opacity: 1 },
        transition: { duration: 0.2, delay: 0 },
      };
    }
    return {
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0.35, ease: EASE_OUT_QUINT, delay },
    };
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-col items-center justify-center gap-5 px-4 py-12 text-center">
      <motion.div {...fadeUp(0)} className="w-full">
        <LibraryAgentsMarquee />
      </motion.div>

      <div className="flex flex-col items-center gap-2">
        <motion.div {...fadeUp(0.05)}>
          <Text variant="h3" className="text-zinc-900">
            You have no agents. Let&apos;s change that.
          </Text>
        </motion.div>
        <motion.div {...fadeUp(0.12)}>
          <Text variant="body" className="text-zinc-500">
            Work with AutoPilot to create one, grab an agent from the
            marketplace, or use the builder to piece one together.
          </Text>
        </motion.div>
      </div>

      <motion.div {...fadeUp(0.2)} className="w-full">
        <Button
          as="NextLink"
          href="/copilot"
          variant="primary"
          size="large"
          className="w-full"
          leftIcon={<SparkleIcon className="h-5 w-5" weight="fill" />}
        >
          Ask AutoPilot
        </Button>
      </motion.div>

      <div className="flex w-full flex-col items-stretch gap-2 sm:flex-row">
        <motion.div {...fadeUp(0.26)} className="flex-1">
          <Button
            as="NextLink"
            href="/marketplace"
            variant="secondary"
            size="large"
            className="w-full"
            leftIcon={<StorefrontIcon className="h-4 w-4" weight="bold" />}
          >
            Browse marketplace
          </Button>
        </motion.div>
        <motion.div {...fadeUp(0.32)} className="flex-1">
          <Button
            as="NextLink"
            href="/build"
            variant="secondary"
            size="large"
            className="w-full"
            leftIcon={<CodeIcon className="h-4 w-4" weight="bold" />}
            rightIcon={
              <span className="ml-1 inline-flex items-center rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700">
                Advanced
              </span>
            }
          >
            Build manually
          </Button>
        </motion.div>
      </div>
    </div>
  );
}
