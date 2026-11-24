/**
 * components/agentgeex/openclaw-link.tsx
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng] Add a "jump to its framework page" link to every Agent
 * (OpenClaw's own session search is poor; go straight there from here).
 *
 * Three forms:
 *   - `variant="icon"`   card corner badge / table row (icon only, hint on hover)
 *   - `variant="button"` detail page header (icon + text)
 *   - `variant="inline"` end of row (icon + "Open", button look)
 *
 * ★ Events: the whole card is clickable (goes to detail), so the link must `stopPropagation`,
 *   otherwise clicking the external link also triggers card navigation → the user is bounced back in-site.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { chatDeepLink, supportsChatDeepLink } from "@/lib/openclaw";

export interface OpenClawLinkProps {
  agentId: string;
  agentType?: string;
  hostId?: string;
  variant?: "icon" | "button" | "inline";
  className?: string;
}

export function OpenClawLink({
  agentId,
  agentType,
  hostId,
  variant = "icon",
  className,
}: OpenClawLinkProps) {
  /* ★ The hook must be before any return — this used to start with `if (...) return null` on the first line. */
  const { t } = useTranslation();
  // Platform detection stays in lib (adding a platform later touches one place); unsupported platforms render nothing
  if (!supportsChatDeepLink(agentType)) return null;
  const href = chatDeepLink(agentId, agentType, hostId);
  if (!href) return null;

  const stop = (e: React.MouseEvent) => e.stopPropagation();
  const label = t("miscShell.openclaw.openIn");

  if (variant === "button") {
    return (
      <Button asChild variant="outline" size="sm" className={cn("gap-1.5", className)}>
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          onClick={stop}
          title={href}
          data-testid={`openclaw-open-${agentId}`}
        >
          <ExternalLink className="h-4 w-4" />
          {t("miscShell.openclaw.openOpenclaw")}
        </a>
      </Button>
    );
  }

  // [2026-11-01 Feng] This used to be a small blue underlined "Open" text.
  // Every other entry in the same list is a button, only this was text — users had to judge row by row whether it was clickable.
  if (variant === "inline") {
    return (
      <Button asChild variant="view" size="sm" className={cn("h-7 gap-1 px-2 text-xs", className)}>
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          onClick={stop}
          title={href}
          data-testid={`openclaw-open-${agentId}`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
          {t("miscShell.openclaw.open")}
        </a>
      </Button>
    );
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      onClick={stop}
      title={`${label} · ${href}`}
      aria-label={label}
      data-testid={`openclaw-open-${agentId}`}
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground",
        "transition-colors hover:bg-accent hover:text-foreground",
        className,
      )}
    >
      <ExternalLink className="h-3.5 w-3.5" />
    </a>
  );
}
