/**
 * JsonView — unified JSON/text display component
 *
 * Background (Feng 2026-10-11):
 *   many places in the detail page display JSON strings (e.g. exec_instruction is actually an embedded
 *   JSON payload), but they used to be rendered with bare <textarea> / <pre> -> no indentation, squeezed
 *   onto one line, poor readability. Requirement: format JSON uniformly wherever it is shown, for a better
 *   result.
 *
 * Strategy:
 *   1. the passed value may be:
 *        - JS object/array -> JSON.stringify(v, null, 2) directly
 *        - JSON string    -> try JSON.parse then pretty-print (key point: embedded JSON strings get formatted)
 *        - non-JSON text  -> show as-is (plain text instructions must not throw)
 *   2. optional showCopy: one-click copy at the top right (copies the formatted text, same convention as
 *      shardkit cases/detail)
 *   3. theme: code (dark terminal look) | muted (light document look), default muted
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Detect and format: returns { text, isJson } */
export function formatJsonLike(value: unknown): { text: string; isJson: boolean } {
  if (value == null) return { text: "", isJson: false };

  // 1. Object / array: pretty directly
  if (typeof value === "object") {
    try {
      return { text: JSON.stringify(value, null, 2), isJson: true };
    } catch {
      return { text: String(value), isJson: false };
    }
  }

  // 2. String: try parsing as JSON (embedded JSON string -> formatted)
  if (typeof value === "string") {
    const s = value.trim();
    if (s && (s.startsWith("{") || s.startsWith("["))) {
      try {
        const parsed = JSON.parse(s);
        return { text: JSON.stringify(parsed, null, 2), isJson: true };
      } catch {
        /* Not valid JSON -> treat as plain text */
      }
    }
    return { text: value, isJson: false };
  }

  // 3. Other primitives
  return { text: String(value), isJson: false };
}

export interface JsonViewProps {
  /** Content to display: object / JSON string / plain text */
  value: unknown;
  /** Whether to show the copy button at the top right */
  showCopy?: boolean;
  /** Appearance: code=dark terminal look; muted=light document look (default) */
  variant?: "code" | "muted";
  /** Custom class (to control height/scroll etc.) */
  className?: string;
  /** Placeholder text for empty values */
  placeholder?: string;
  /** data-testid */
  "data-testid"?: string;
}

export function JsonView({
  value,
  showCopy = true,
  variant = "muted",
  className,
  placeholder = "—",
  "data-testid": testId,
}: JsonViewProps) {
  const { t } = useTranslation();
  const { text, isJson } = React.useMemo(() => formatJsonLike(value), [value]);
  const [copied, setCopied] = React.useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* Clipboard unavailable -> silent */
    }
  };

  const body = text || placeholder;

  return (
    <div className="relative">
      {showCopy && text ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="absolute right-1.5 top-1.5 z-10 h-7 w-7 opacity-70 hover:opacity-100"
          aria-label={t("miscShell.jsonView.copy")}
          data-testid={testId ? `${testId}-copy` : undefined}
          onClick={onCopy}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      ) : null}
      <pre
        data-testid={testId}
        data-json={isJson ? "1" : undefined}
        className={cn(
          "overflow-auto whitespace-pre-wrap break-words rounded-md p-3 font-mono text-xs leading-relaxed",
          variant === "code"
            ? "bg-code text-success"
            : "bg-muted/30 text-foreground",
          showCopy && text ? "pr-9" : "",
          className
        )}
      >
        {body}
      </pre>
    </div>
  );
}
