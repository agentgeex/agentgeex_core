/**
 * MarkdownView — read-only Markdown rendering (follows the site theme).
 *
 * Same source as MarkdownEditor (@uiw/react-markdown-preview), reusing the same styles,
 * for "display only, no editing" cases (e.g. the body in the skill-library detail dialog).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import MarkdownPreview from "@uiw/react-markdown-preview";
import { cn } from "@/lib/utils";

export function MarkdownView({
  source,
  compact = false,
  bare = false,
  className,
}: {
  source: string;
  /** Compact mode: narrow contexts like dialogs; title/font size tightened (see index.css [data-md-compact]) */
  compact?: boolean;
  /**
   * Bare mode: no outer frame / background / large padding — for embedding inside list rows
   * (e.g. the history in the send dialog, where each row is already narrow and another frame would be a "box in a box").
   * [2026-11-14 11:15 Feng] History replies need formatting, but rows are tight, hence this switch.
   */
  bare?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const [dark, setDark] = React.useState(() =>
    typeof document !== "undefined" ? document.documentElement.classList.contains("dark") : true
  );

  // Follows the site theme
  React.useEffect(() => {
    const el = document.documentElement;
    const observer = new MutationObserver(() => setDark(el.classList.contains("dark")));
    observer.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return (
    <div
      className={cn(
        "agentgeex-md overflow-hidden",
        bare ? "bg-transparent" : "rounded-lg border bg-background/60",
        className,
      )}
      data-color-mode={dark ? "dark" : "light"}
      {...(compact ? { "data-md-compact": "" } : {})}
    >
      <MarkdownPreview
        source={source || t("miscShell.markdownView.empty")}
        style={{ background: "transparent", padding: bare ? "0" : "12px 14px" }}
        wrapperElement={{ "data-color-mode": dark ? "dark" : "light" }}
      />
    </div>
  );
}
