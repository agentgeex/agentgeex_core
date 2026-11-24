/**
 * RichTextView — unified "long text view block" (Markdown / plain text channels)
 *
 * Background (Feng 2026-10-11, scope widened 2026-10-30):
 *   In detail pages, every "big chunk of content" block (instructions / body / summary / instruction template / live output)
 *   used to be written differently each time: some bare <pre>, some MarkdownView, some TextBlock + JsonView,
 *   with 11 / 12 / 13 / 16px font sizes coexisting, and whether the container had a border, whether long content
 *   stretched the page or scrolled inside, all page-dependent.
 *   The requirement is to unify into one thing: **the same kind of content looks and behaves the same on any page**.
 *
 * Rules after unification (not aesthetics, but predictable reading behaviour):
 *   · Frame: always a bordered rounded container; overflow **scrolls inside the frame**, does not stretch the page
 *     (measured: a project instruction 3840px tall pushes other sections and "raw data" several screens away)
 *   · Full text: top-right "expand" → dialog with the full text (rendered there too, no source view)
 *   · Two content channels, chosen by **content nature**, not by page:
 *       mode="markdown"  human prose (instructions / body / skill body / execution summary) → render as Markdown
 *       mode="text"      machine text (instruction templates / composed results / payload / logs) → monospace raw + copy
 *     Machine text must never go through Markdown: `{date}`, indentation, `#`, `*` in a template would be eaten as syntax.
 *   · Empty state: the caller gives one concrete sentence ("summary not written back yet" beats "no content"),
 *     shown in the same place, leaving no blank frame.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, Maximize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MarkdownView } from "@/components/ui/markdown-view";
import { formatJsonLike } from "@/components/ui/json-view";
import { cn } from "@/lib/utils";

/** Text for display: JSON string → pretty; others as-is (Markdown source / plain text) */
function toDisplay(value: unknown): string {
  return formatJsonLike(value).text;
}

export type RichTextMode = "markdown" | "text";

export function RichTextView({
  title,
  value,
  mode = "markdown",
  className,
  bodyClassName,
  testId,
  emptyText,
  showCopy,
}: {
  /** Block title; **when omitted** the title row is not rendered, and "expand / copy" float at the content's top right
   *  (the detail page already has a DetailSection title above; writing it again is duplication) */
  title?: string;
  /** Content: string (Markdown / template source / JSON string) / object */
  value: unknown;
  /** Content channel: prose uses markdown, machine text uses text (default markdown) */
  mode?: RichTextMode;
  className?: string;
  /** Height class of the content scroll area (default 240px, overflow scrolls inside the frame) */
  bodyClassName?: string;
  testId?: string;
  emptyText?: string;
  /** Copy button; by default shown only on the text channel (for prose, select-and-copy is more natural) */
  showCopy?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const text = React.useMemo(() => toDisplay(value), [value]);
  const hasText = !!text && !!text.trim();
  const withCopy = showCopy ?? mode === "text";

  const onCopy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* Clipboard unavailable → silent (consistent with JsonView) */
    }
  };

  const toolbar = (
    <div
      className={cn(
        "flex items-center gap-1",
        // With no title row it floats at the content's top right: give the button a translucent background so it stays readable over text
        !title && "absolute right-1.5 top-1.5 z-10 rounded-md bg-background/85 p-0.5 backdrop-blur-sm",
      )}
    >
      {withCopy && (
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-6 w-6 shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={t("miscShell.richText.copyAll")}
          data-testid={testId ? `${testId}-copy` : undefined}
          onClick={onCopy}
          disabled={!hasText}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      )}
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="h-6 w-6 shrink-0 text-muted-foreground hover:text-foreground"
        aria-label={t("miscShell.richText.expand")}
        data-testid={testId ? `${testId}-expand` : undefined}
        onClick={() => setOpen(true)}
        disabled={!hasText}
      >
        <Maximize2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );

  return (
    <div className={cn("flex flex-col gap-2", title && "rounded-lg border p-3", className)} data-testid={testId}>
      {title ? (
        <div className="flex items-center gap-2">
          <div className="text-xs font-semibold text-muted-foreground">{title}</div>
          <div className="ml-auto">{toolbar}</div>
        </div>
      ) : null}

      {/* Content: framed + scrolls inside the frame; rendered by channel */}
      <div
        className={cn("relative min-h-0 overflow-auto rounded-md border bg-background/60", bodyClassName ?? "h-60")}
        data-testid={testId ? `${testId}-body` : undefined}
      >
        {!title ? toolbar : null}
        {hasText ? (
          mode === "text" ? (
            <pre className="whitespace-pre-wrap break-words p-3 font-mono text-[13px] leading-relaxed text-foreground">
              {text}
            </pre>
          ) : (
            <MarkdownView source={text} className="border-0 bg-transparent" />
          )
        ) : (
          <div className="p-3 text-sm text-muted-foreground">
        {emptyText ?? t("miscShell.richText.empty")}
      </div>
        )}
      </div>

      {/* Dialog: full content (rendered the same, no source view) */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[88vh] max-w-4xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{title ?? t("miscShell.richText.fullTitle")}</DialogTitle>
          </DialogHeader>
          {hasText ? (
            mode === "text" ? (
              <pre className="whitespace-pre-wrap break-words rounded-md border bg-background/60 p-3 font-mono text-[13px] leading-relaxed">
                {text}
              </pre>
            ) : (
              <MarkdownView source={text} className="border-0 bg-transparent" />
            )
          ) : (
            <p className="text-sm text-muted-foreground">{emptyText ?? t("miscShell.richText.empty")}</p>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
