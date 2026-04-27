"use client";

import React, { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import DOMPurify from "dompurify";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";
import { CaretDown, CaretRight } from "@phosphor-icons/react";
import type {
  OutputRenderer,
  OutputMetadata,
  DownloadContent,
  CopyContent,
} from "../types";
import "katex/dist/katex.min.css";
import "highlight.js/styles/github-dark.css";

// ---------------------------------------------------------------------------
// Notebook JSON types (nbformat 4)
// ---------------------------------------------------------------------------

interface NotebookOutput {
  output_type: "stream" | "display_data" | "execute_result" | "error";
  // stream
  name?: "stdout" | "stderr";
  text?: string | string[];
  // display_data / execute_result
  data?: Record<string, string | string[]>;
  execution_count?: number | null;
  // error
  ename?: string;
  evalue?: string;
  traceback?: string[];
}

interface NotebookCell {
  cell_type: "code" | "markdown" | "raw";
  source: string | string[];
  outputs?: NotebookOutput[];
  execution_count?: number | null;
  metadata?: Record<string, unknown>;
}

interface NotebookMetadata {
  kernelspec?: {
    language?: string;
    display_name?: string;
    name?: string;
  };
  language_info?: {
    name?: string;
    version?: string;
  };
}

interface Notebook {
  nbformat: number;
  nbformat_minor?: number;
  metadata?: NotebookMetadata;
  cells: NotebookCell[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function joinSource(source: string | string[]): string {
  return Array.isArray(source) ? source.join("") : source;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
}

function isSource(value: unknown): value is string | string[] {
  return typeof value === "string" || isStringArray(value);
}

function isExecutionCount(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

function isNotebookOutput(value: unknown): value is NotebookOutput {
  if (!isRecord(value)) return false;

  const outputType = value.output_type;
  if (
    outputType !== "stream" &&
    outputType !== "display_data" &&
    outputType !== "execute_result" &&
    outputType !== "error"
  ) {
    return false;
  }

  if (
    value.name !== undefined &&
    value.name !== "stdout" &&
    value.name !== "stderr"
  ) {
    return false;
  }

  if (value.text !== undefined && !isSource(value.text)) return false;

  if (
    value.data !== undefined &&
    (!isRecord(value.data) || !Object.values(value.data).every(isSource))
  ) {
    return false;
  }

  if (
    value.execution_count !== undefined &&
    !isExecutionCount(value.execution_count)
  ) {
    return false;
  }

  if (value.ename !== undefined && typeof value.ename !== "string")
    return false;
  if (value.evalue !== undefined && typeof value.evalue !== "string") {
    return false;
  }
  if (value.traceback !== undefined && !isStringArray(value.traceback)) {
    return false;
  }

  return true;
}

function isNotebookCell(value: unknown): value is NotebookCell {
  if (!isRecord(value)) return false;

  if (
    value.cell_type !== "code" &&
    value.cell_type !== "markdown" &&
    value.cell_type !== "raw"
  ) {
    return false;
  }

  if (!isSource(value.source)) return false;

  if (
    value.outputs !== undefined &&
    (!Array.isArray(value.outputs) || !value.outputs.every(isNotebookOutput))
  ) {
    return false;
  }

  if (
    value.execution_count !== undefined &&
    !isExecutionCount(value.execution_count)
  ) {
    return false;
  }

  if (value.metadata !== undefined && !isRecord(value.metadata)) return false;

  return true;
}

function sanitizeNotebookMarkup(markup: string): string {
  return DOMPurify.sanitize(markup, {
    USE_PROFILES: { html: true, svg: true, svgFilters: true },
  });
}

function SanitizedNotebookMarkup({
  className,
  markup,
}: {
  className: string;
  markup: string;
}) {
  const [sanitizedMarkup, setSanitizedMarkup] = useState("");

  useEffect(() => {
    setSanitizedMarkup(sanitizeNotebookMarkup(markup));
  }, [markup]);

  if (!sanitizedMarkup) return null;

  return (
    <div
      className={className}
      dangerouslySetInnerHTML={{ __html: sanitizedMarkup }}
    />
  );
}

function parseNotebook(value: unknown): Notebook | null {
  try {
    let obj: unknown = value;
    if (typeof value === "string") {
      obj = JSON.parse(value);
    }
    if (
      isRecord(obj) &&
      typeof obj.nbformat === "number" &&
      Array.isArray(obj.cells) &&
      obj.cells.every(isNotebookCell) &&
      (obj.metadata === undefined || isRecord(obj.metadata))
    ) {
      return obj as unknown as Notebook;
    }
  } catch {
    // not a notebook
  }
  return null;
}

// ---------------------------------------------------------------------------
// canRender
// ---------------------------------------------------------------------------

function canRenderNotebook(value: unknown, metadata?: OutputMetadata): boolean {
  if (
    metadata?.type === "notebook" ||
    metadata?.filename?.toLowerCase().endsWith(".ipynb") ||
    metadata?.mimeType === "application/x-ipynb+json"
  ) {
    return parseNotebook(value) !== null;
  }
  return parseNotebook(value) !== null;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function NotebookOutputBlock({ output }: { output: NotebookOutput }) {
  if (output.output_type === "error") {
    const traceback = (output.traceback ?? [])
      .map((line) =>
        // Strip ANSI escape codes for clean display
        line.replace(/\x1b\[[0-9;]*m/g, ""),
      )
      .join("\n");
    return (
      <div className="mt-1 rounded border border-red-800/40 bg-red-950/30 p-2 font-mono text-xs text-red-400">
        <div className="font-semibold">
          {output.ename}: {output.evalue}
        </div>
        {traceback && (
          <pre className="mt-1 whitespace-pre-wrap break-words opacity-80">
            {traceback}
          </pre>
        )}
      </div>
    );
  }

  if (output.output_type === "stream") {
    const text = joinSource(output.text ?? "");
    if (!text) return null;
    const isStderr = output.name === "stderr";
    return (
      <pre
        className={`mt-1 whitespace-pre-wrap break-words rounded p-2 font-mono text-xs ${
          isStderr
            ? "border border-yellow-800/40 bg-yellow-950/30 text-yellow-300"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {text}
      </pre>
    );
  }

  if (
    output.output_type === "display_data" ||
    output.output_type === "execute_result"
  ) {
    const data = output.data ?? {};

    // Prefer image/png
    const png = data["image/png"];
    if (png) {
      const src = `data:image/png;base64,${joinSource(png)}`;
      return (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt="Cell output"
          className="mt-1 max-w-full rounded"
          loading="lazy"
        />
      );
    }

    // image/jpeg
    const jpeg = data["image/jpeg"];
    if (jpeg) {
      const src = `data:image/jpeg;base64,${joinSource(jpeg)}`;
      return (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt="Cell output"
          className="mt-1 max-w-full rounded"
          loading="lazy"
        />
      );
    }

    // image/svg+xml
    const svg = data["image/svg+xml"];
    if (svg) {
      return (
        <SanitizedNotebookMarkup className="mt-1" markup={joinSource(svg)} />
      );
    }

    // text/html - render sanitized
    const html = data["text/html"];
    if (html) {
      return (
        <SanitizedNotebookMarkup
          className="mt-1 overflow-x-auto rounded bg-muted p-2 text-sm"
          markup={joinSource(html)}
        />
      );
    }

    // text/plain fallback
    const plain = data["text/plain"];
    if (plain) {
      return (
        <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-muted p-2 font-mono text-xs text-muted-foreground">
          {joinSource(plain)}
        </pre>
      );
    }
  }

  return null;
}

function CollapsibleOutputs({ outputs }: { outputs: NotebookOutput[] }) {
  const [open, setOpen] = useState(true);
  if (!outputs.length) return null;

  return (
    <div className="ml-4 border-l-2 border-muted pl-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? (
          <CaretDown className="size-3" />
        ) : (
          <CaretRight className="size-3" />
        )}
        {open ? "Hide" : "Show"} output
      </button>
      {open && (
        <div className="mt-1 flex flex-col gap-1">
          {outputs.map((out, i) => (
            <NotebookOutputBlock key={i} output={out} />
          ))}
        </div>
      )}
    </div>
  );
}

function CodeCell({
  cell,
  language,
}: {
  cell: NotebookCell;
  language: string;
}) {
  const source = joinSource(cell.source);
  const execCount = cell.execution_count;

  return (
    <div className="group flex gap-2">
      {/* Execution count gutter */}
      <div className="w-10 shrink-0 select-none pt-2 text-right font-mono text-xs text-muted-foreground">
        {execCount != null ? `[${execCount}]:` : "[ ]:"}
      </div>

      <div className="min-w-0 flex-1">
        {/* Source */}
        <div className="relative">
          <div className="absolute right-2 top-1.5 z-10 rounded bg-background/80 px-1.5 py-0.5 text-xs text-muted-foreground">
            {language}
          </div>
          <pre className="overflow-x-auto rounded bg-muted p-3 pr-16">
            <code className={`language-${language} text-sm`}>{source}</code>
          </pre>
        </div>

        {/* Outputs */}
        {cell.outputs && cell.outputs.length > 0 && (
          <CollapsibleOutputs outputs={cell.outputs} />
        )}
      </div>
    </div>
  );
}

function MarkdownCell({ cell }: { cell: NotebookCell }) {
  const source = joinSource(cell.source);
  return (
    <div className="px-2 py-1">
      <ReactMarkdown
        className="prose prose-sm dark:prose-invert max-w-none"
        remarkPlugins={[
          remarkGfm,
          [remarkMath, { singleDollarTextMath: false }],
        ]}
        rehypePlugins={[[rehypeKatex, { strict: false }], rehypeHighlight]}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}

function RawCell({ cell }: { cell: NotebookCell }) {
  const source = joinSource(cell.source);
  if (!source.trim()) return null;
  return (
    <pre className="whitespace-pre-wrap break-words px-2 py-1 font-mono text-xs text-muted-foreground">
      {source}
    </pre>
  );
}

function NotebookViewer({ notebook }: { notebook: Notebook }) {
  const language =
    notebook.metadata?.kernelspec?.language ??
    notebook.metadata?.language_info?.name ??
    "python";

  const version = notebook.metadata?.language_info?.version;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-background p-3">
      {/* Notebook header */}
      <div className="flex items-center gap-2 border-b border-border pb-2 text-xs text-muted-foreground">
        <span className="rounded bg-muted px-2 py-0.5 font-mono font-medium capitalize">
          {language}
          {version ? ` ${version}` : ""}
        </span>
        <span>nbformat {notebook.nbformat}</span>
        <span>·</span>
        <span>{notebook.cells.length} cells</span>
      </div>

      {/* Cells */}
      {notebook.cells.map((cell, idx) => (
        <div
          key={idx}
          className="rounded border border-transparent transition-colors hover:border-border/50"
        >
          {cell.cell_type === "code" && (
            <CodeCell cell={cell} language={language} />
          )}
          {cell.cell_type === "markdown" && <MarkdownCell cell={cell} />}
          {cell.cell_type === "raw" && <RawCell cell={cell} />}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Renderer interface implementations
// ---------------------------------------------------------------------------

function renderNotebook(
  value: unknown,
  _metadata?: OutputMetadata,
): React.ReactNode {
  const notebook = parseNotebook(value);
  if (!notebook) return null;
  return <NotebookViewer notebook={notebook} />;
}

function getCopyContentNotebook(
  value: unknown,
  _metadata?: OutputMetadata,
): CopyContent | null {
  const raw =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return {
    mimeType: "application/json",
    data: raw,
    fallbackText: raw,
    alternativeMimeTypes: ["text/plain"],
  };
}

function getDownloadContentNotebook(
  value: unknown,
  metadata?: OutputMetadata,
): DownloadContent | null {
  const raw =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const blob = new Blob([raw], { type: "application/x-ipynb+json" });
  return {
    data: blob,
    filename: metadata?.filename ?? "notebook.ipynb",
    mimeType: "application/x-ipynb+json",
  };
}

function isConcatenableNotebook(
  _value: unknown,
  _metadata?: OutputMetadata,
): boolean {
  return false;
}

export const notebookRenderer: OutputRenderer = {
  name: "NotebookRenderer",
  priority: 36,
  canRender: canRenderNotebook,
  render: renderNotebook,
  getCopyContent: getCopyContentNotebook,
  getDownloadContent: getDownloadContentNotebook,
  isConcatenable: isConcatenableNotebook,
};
