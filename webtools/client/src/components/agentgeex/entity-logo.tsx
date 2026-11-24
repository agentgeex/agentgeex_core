/**
 * Entity logo (project / task)
 * ────────────────────────────────────────────────────────────────
 * Same design language as `agent-avatar.tsx`, for the project / task object types:
 *
 * Value priority:
 *   1. `logoUrl` (backend `project_logo` / `task_logo`)
 *   2. Local brand image (`public/brand/projects|tasks/*.png`, see lib/brand-logos.ts)
 *   3. **Initials + deterministic color** (hashed by id / task_labs; the same object always gets the same color and letter)
 *
 * ★ Tasks are deduped by `task_labs`: task_labs repeat (general 76 rows, watchdog_alert 76 rows…),
 *   so all tasks with the same labs share one logo. When passing `task_id`, the caller either gives labs first
 *   or passes id directly — the component resolves with labs first, id second.
 *
 * ★ A fallback is mandatory (Feng 2026-10-20): images may not be generated yet; without a fallback the page is a blank slot.
 */
import * as React from "react";
import { cn } from "@/lib/utils";
import { agentInitials } from "@/components/agentgeex/agent-avatar";
import { resolveBrandProjectLogo, resolveBrandTaskLogo } from "@/lib/brand-logos";
import { getTaskLabs } from "@/providers/agentgeex";

/** Deterministic color (design tokens only, avoid raw palettes; the same key always hits the same slot) */
const TONES = [
  "bg-info/15 text-info",
  "bg-chart-1/15 text-chart-1",
  "bg-chart-2/15 text-chart-2",
  "bg-chart-3/15 text-chart-3",
  "bg-chart-4/15 text-chart-4",
  "bg-chart-5/15 text-chart-5",
  "bg-chart-6/15 text-chart-6",
  "bg-success/15 text-success",
];

/** 32-bit string hash (stable, the same key always gives the same value) */
function hashIndex(key: string, mod: number): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return Math.abs(h) % mod;
}

export type EntityLogoKind = "project" | "task";

export interface EntityLogoProps extends React.HTMLAttributes<HTMLDivElement> {
  kind: EntityLogoKind;
  /** Object id (project_id / task_id) — resolution key and color key */
  id?: string;
  /** The task's task_labs (used to dedupe the image; ignored for projects) */
  labs?: string;
  /** Display name (initials taken from it; tasks usually pass task_name or labs) */
  name?: string;
  /** Backend logo field (project_logo / task_logo) */
  logoUrl?: string;
  /** Frame size, e.g. "h-8 w-8" */
  className?: string;
  /** Initials font size, e.g. "text-xs" */
  textClassName?: string;
}

/**
 * Project / task logo. testid: `<kind>-logo-<key>`, `data-logo-mode=image|brand|initials`.
 */
export function EntityLogo({
  kind, id, labs, name, logoUrl, className, textClassName, ...rest
}: EntityLogoProps) {
  /* Tasks dedupe by task_labs: when labs is not given explicitly, look it up by task_id (rows sharing labs share one image) */
  const taskLabs = kind === "task" ? labs || getTaskLabs(id) : "";
  const key = (kind === "task" ? taskLabs || id : id) || name || "";
  const brand = kind === "project" ? resolveBrandProjectLogo(id) : resolveBrandTaskLogo(taskLabs);
  const src = logoUrl || brand;
  /* In the manifest but the file is missing/broken → onError falls back to initials, avoiding a broken-image placeholder */
  const [broken, setBroken] = React.useState(false);
  React.useEffect(() => { setBroken(false); }, [src]);

  const glyphName = kind === "task" ? taskLabs || name || id : name || id;
  const label = agentInitials(glyphName, key);
  const testid = `${kind}-logo-${key || "unknown"}`;

  if (src && !broken) {
    return (
      <div
        data-testid={testid}
        data-logo-mode={logoUrl ? "image" : "brand"}
        className={cn("flex shrink-0 items-center justify-center overflow-hidden rounded-lg", className ?? "h-8 w-8")}
        {...rest}
      >
        {/* Brand images are white-background png; on dark theme put bg-white underneath so the grey base does not show through */}
        <img
          src={src}
          alt={name || key}
          className="h-full w-full bg-white object-cover"
          loading="lazy"
          onError={() => setBroken(true)}
        />
      </div>
    );
  }

  return (
    <div
      data-testid={testid}
      data-logo-mode="initials"
      title={name || key}
      aria-label={name || key}
      className={cn(
        "flex shrink-0 select-none items-center justify-center rounded-lg font-semibold",
        TONES[hashIndex(key, TONES.length)],
        className ?? "h-8 w-8",
        textClassName ?? "text-xs"
      )}
      {...rest}
    >
      {label}
    </div>
  );
}
