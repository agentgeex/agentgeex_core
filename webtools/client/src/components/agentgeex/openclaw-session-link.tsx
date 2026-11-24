/**
 * components/agentgeex/openclaw-session-link.tsx
 * ────────────────────────────────────────────────────────────────
 * [2026-10-28 Feng] Sessions and running tasks must be able to 'click in and locate that OpenClaw session'.
 *
 * The session id ↔ URL mapping lives in one place: `lib/openclaw.ts#sessionDeepLink`:
 *   `agent:{agent_id}:{slug}`  →  `/chat/{agent_id}/{slug}`
 * This component only 'renders it as a link' + stops propagation (table rows are clickable; clicking the link must not follow the row).
 *
 * ★ Exactly **one** rendering: icon + 'Open session' (set by Feng 2026-11-03 20:36).
 *
 *   Evolution log (stop flip-flopping; think before changing):
 *     `inline` (blue underlined text)  → removed 2026-11-01 (duplicated the row action buttons)
 *     `button` (icon + text)           → removed 2026-11-03 20:12 (decided then to keep only the icon site-wide)
 *     `icon` (icon only)               → removed 2026-11-03 20:36
 *   Final conclusion: **an icon alone cannot tell you where you are going** (especially in isolated spots like a page
 *   header, a little box just makes people guess). The icon handles 'recognition', the text handles 'understanding'.
 *
 * ★ Single form = no `variant` input. Use `className` to adjust position/spacing.
 */
import * as React from "react";
import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { parseSessionId, sessionDeepLink } from "@/lib/openclaw";

export interface OpenClawSessionLinkProps {
  /** Full session id, e.g. `agent:book_pydev_850472:book_code_engineer` */
  sessionId?: string;
  /** The session's owning Agent (parsed from sessionId when omitted) */
  agentId?: string;
  agentType?: string;
  hostId?: string;
  /** Copy (default 'Open session'). Unless there is an extremely special reason, do not change it — one name site-wide. */
  label?: string;
  className?: string;
}

export function OpenClawSessionLink({
  sessionId,
  agentId,
  agentType,
  hostId,
  label,
  className,
}: OpenClawSessionLinkProps) {
  const { t } = useTranslation();
  const parsed = parseSessionId(sessionId);
  const aid = (agentId || parsed?.agentId || "").trim();
  if (!aid) return null;

  // Pass undefined (not an empty string) → let sessionDeepLink decide whether to use registry info
  const href = sessionDeepLink(sessionId, agentType, hostId);
  if (!href) return null;

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      onClick={(e) => e.stopPropagation()}
      title={t("common.openSessionIn", { href })}
      data-testid={`openclaw-session-${sessionId || aid}`}
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-1",
        "text-xs font-medium text-primary underline-offset-2",
        "hover:bg-accent hover:underline",
        className,
      )}
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {label ?? t("common.openSession")}
    </a>
  );
}
