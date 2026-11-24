/**
 * components/agentgeex/row-actions.tsx — unified action buttons inside list rows
 * ────────────────────────────────────────────────────────────────
 * [2026-11-01 Feng] "Go through all the tables on the platform and turn these clickable links into buttons,
 *   don't leave them as text like this, that makes them inconsistent, let's unify it first."
 *
 * The problem is not "links look bad", it is that **the same action looks different on different pages**:
 *   compute org is an orange "Edit" button, quick command is an icon button,
 *   while task/session/skill/plan/project are still blue underlined text —
 *   users have to re-judge "can I click here" page by page.
 *
 * Unified rules (copying the quick command set Feng approved, inventing nothing):
 *   · Entries with no side effects (edit / detail)       → icon button, takes no width
 *   · Actions that really do things (run / send message / urge / flow chart) → icon + text, avoid misclicks
 *   · Dangerous actions (delete / archive)               → red
 *
 * Colors use only existing tokens (Button's run/view/edit/back/destructive),
 * and always solid fill — outlined buttons are invisible on white pages; this has been reworked twice already.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Info, Pencil, Play, Send, Trash2, Network, Megaphone, BellRing, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type RowActionKind =
  | "view"      // Details (rarely used: clicking the row is the detail)
  | "edit"      // Edit
  | "run"       // Run / Execute
  | "send"      // Send message
  | "flow"      // Flow chart
  | "nudge"     // Urge
  | "ack"       // Remind to acknowledge
  | "open"      // Jump to the framework/platform console (external link, new tab)
  | "delete";   // Delete / Archive

/* Text keeps only the key: this table is a module-level constant, the hook is unavailable at load time. */
const PRESET: Record<RowActionKind, { icon: React.ElementType; variant: "view" | "edit" | "run" | "back" | "destructive"; labelKey: string; withText: boolean }> = {
  view: { icon: Info, variant: "view", labelKey: "rowActions.view", withText: false },
  edit: { icon: Pencil, variant: "edit", labelKey: "rowActions.edit", withText: false },
  run: { icon: Play, variant: "run", labelKey: "rowActions.run", withText: true },
  send: { icon: Send, variant: "run", labelKey: "rowActions.send", withText: true },
  flow: { icon: Network, variant: "view", labelKey: "rowActions.flow", withText: true },
  nudge: { icon: Megaphone, variant: "run", labelKey: "rowActions.nudge", withText: true },
  ack: { icon: BellRing, variant: "edit", labelKey: "rowActions.ack", withText: true },
  // [2026-11-01 Feng] "Open" = jump to the console of the platform the session belongs to.
  // With text: it leaves this console, so a misclick costs more than "Edit".
  open: { icon: ExternalLink, variant: "view", labelKey: "rowActions.open", withText: true },
  delete: { icon: Trash2, variant: "destructive", labelKey: "rowActions.delete", withText: false },
};

export interface RowActionProps {
  kind: RowActionKind;
  /** Click action; pick one of it and to */
  onClick?: (e: React.MouseEvent) => void;
  /** In-site navigation; uses Link semantics (middle click opens a new tab, address copyable), still looks like a button */
  to?: string;
  /**
   * External link (new tab). When given, renders as a real `<a href>` —
   * not "a window.open stuffed into a button": right-click copy address, middle click and screen readers all rely on this href.
   */
  href?: string;
  /** Override the default text (e.g. "Edit task") */
  label?: string;
  /** Hover tooltip. For required items, state consequences like "who will receive this message" */
  title?: string;
  disabled?: boolean;
  testId?: string;
  className?: string;
}

/**
 * A single row action button.
 *
 * ★ Always stopPropagation internally: the table row itself is the click target (click row → detail),
 *   without it "click Edit" would jump to detail first and then run edit — wrong order, and it looks like nothing happened.
 */
export function RowAction({
  kind,
  onClick,
  to,
  href,
  label,
  title,
  disabled,
  testId,
  className,
}: RowActionProps) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const preset = PRESET[kind];
  const Icon = preset.icon;
  const text = label ?? t(preset.labelKey);

  const handle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (disabled) {
      e.preventDefault();
      return;
    }
    if (to) {
      // When a modifier key is held, hand it to the browser (new tab), do not hijack it into in-site navigation
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(to);
      return;
    }
    onClick?.(e);
  };

  if (href && !disabled) {
    return (
      <Button asChild size="sm" variant={preset.variant} className={cn("h-7 px-2", className)}>
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          onClick={(e) => e.stopPropagation()}
          title={title}
          data-testid={testId}
        >
          <Icon className={cn("h-3.5 w-3.5", preset.withText && "mr-1")} />
          {preset.withText ? text : null}
        </a>
      </Button>
    );
  }

  return (
    <Button
      size="sm"
      variant={preset.variant}
      disabled={disabled}
      title={title}
      onClick={handle}
      className={cn("h-7 px-2", className)}
      data-testid={testId}
      // When to exists, provide real link semantics (right-click copy address / middle click opens new)
      {...(to ? { "data-href": to, role: "link", tabIndex: 0 } : {})}
    >
      <Icon className={cn("h-3.5 w-3.5", preset.withText && "mr-1")} />
      {preset.withText ? text : null}
    </Button>
  );
}

/** Row action container: unified spacing, so no page writes its own gap */
export function RowActions({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center justify-end gap-1">{children}</div>;
}
