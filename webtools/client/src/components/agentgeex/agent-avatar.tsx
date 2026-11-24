/**
 * Agent Avatar
 * ────────────────────────────────────────────────────────────────
 * ★ Convention (Feng 2026-10-16):
 *   Agent **avatar slots** must not use an "agent framework/platform logo" (e.g. the openclaw mark) --
 *   a platform mark is an "identifier", not an "avatar"; do not mix the two.
 *
 * Resolution priority (2nd tier added 2026-10-19):
 *   1. `avatarUrl` (from `agx_agent_registry.agent_avatar`, present once the backend exposes it)
 *      -> render the image;
 *   2. local brand avatar `public/brand/avatars/<id>.png` (see lib/brand-avatars.ts,
 *      filename = object id, assistants carry the `case_` prefix) -> render the image;
 *   3. neither -> **initials + deterministic color** (hash by agent_id, same agent always same color and
 *      letters).
 *
 * ★ Once the backend tool is ready (Feng is already preparing it), the BFF only needs to expose
 *   `agentAvatar`, and this component switches to the real avatar automatically -- one change covers
 *   card/detail/session page, nothing else needs touching.
 *
 * ui-quality-gate: allow rawPalette
 *   (exemption rationale: the PALETTE below is a **semantics-free data-visualization palette**, used only to
 *     "tell different agents apart"; the 13 colors must be mutually distinguishable, they are not
 *     state/semantic colors and cannot be collapsed into success/warning/info tokens.)
 */
import * as React from "react";
import { cn } from "@/lib/utils";
import { resolveBrandAvatar } from "@/lib/brand-avatars";

/**
 * Initials fallback rules (finalized by Feng 2026-10-19 19:02):
 *   without an image use the default -- the default matters, a newly added agent cannot have an image
 *   right away.
 *
 *   ① the name **starts with a CJK character / kana** -> take **that 1 character**
 *      (e.g. "Resource Completion Specialist" written in CJK -> its first character)
 *   ② the name **starts with a Latin word** followed by CJK (e.g. "SEM tools-lead" / "UI frontend-lead")
 *      -> take only the leading Latin word: all-caps abbreviations as-is (<=3 chars "SEM" / "UI"),
 *         otherwise the first 2 chars with the first capitalized ("AgentGeeX" -> Ag)
 *   ③ **pure Latin multi-word** -> initials of the two words ("Agent OS" -> AO)
 *   ④ single Latin word -> first 2 chars ("OpenClaw" -> Op)
 *   ⑤ empty -> "?"
 *
 *   Counterexamples (the old implementation produced such mixes, now fixed): "SEM tools-lead" used to
 *   render as "S" + one CJK char, "AgentGeeX defect-hunter" as "A" + one CJK char -- mixed Latin/CJK
 *   reads like gibberish, never mix.
 */
const CJK_RE = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const isCjk = (ch?: string) => !!ch && CJK_RE.test(ch);

export function agentInitials(name?: string, id?: string): string {
  const src = (name || id || "").trim();
  if (!src) return "?";
  const chars = Array.from(src);
  if (isCjk(chars[0])) return chars[0];

  const parts = src.split(/[\s_\-./]+/).filter(Boolean);
  const head = parts[0] ?? src;
  const second = parts[1];

  // ② Latin word + CJK, or itself a single segment -> use only that leading Latin word
  if (!second || isCjk(Array.from(second)[0])) {
    if (/^[A-Z0-9]{2,4}$/.test(head)) return head.slice(0, 3);          // Abbreviations as-is: SEM / UI / AI
    const h = Array.from(head);
    return (h[0].toUpperCase() + (h[1] ?? "")).slice(0, 2);              // AgentGeeX → Ag
  }

  // ③ pure Latin multi-word -> initials of the two words
  return (head[0] + second[0]).toUpperCase();
}

/** 32-bit string hash (stable, same key always same value) */
function hashIndex(key: string, mod: number): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return Math.abs(h) % mod;
}

/** Deterministic palette (literal values, easy for Tailwind to scan; same agent always lands in the same cell) */
const PALETTE = [
  "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  "bg-lime-500/15 text-lime-700 dark:text-lime-400",
  "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  "bg-teal-500/15 text-teal-600 dark:text-teal-400",
  "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400",
  "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400",
  "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  "bg-fuchsia-500/15 text-fuchsia-600 dark:text-fuchsia-400",
  "bg-pink-500/15 text-pink-600 dark:text-pink-400",
];

export interface AgentAvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Agent display name (preferred source for initials) */
  name?: string;
  /** Agent ID (initials fallback + color hash key) */
  id?: string;
  /** Avatar URL (`agent_avatar`, initials when empty) */
  avatarUrl?: string;
  /** Frame size, e.g. "h-8 w-8" */
  className?: string;
  /** Initials font size, e.g. "text-xs" */
  textClassName?: string;
}

/**
 * Agent avatar: use the image when `avatarUrl` exists, otherwise initials + deterministic color.
 * testid: `agent-avatar-<id>` (with `data-avatar-mode=image|initials`).
 */
export function AgentAvatar({
  name, id, avatarUrl, className, textClassName, ...rest
}: AgentAvatarProps) {
  const key = id || name || "";
  const label = agentInitials(name, id);
  const testid = `agent-avatar-${id || "unknown"}`;
  /* Avatar source: backend field first, then local brand image, then initials */
  const brandSrc = avatarUrl || resolveBrandAvatar(id) || resolveBrandAvatar(name);
  const mode = avatarUrl ? "image" : brandSrc ? "brand" : "initials";

  if (brandSrc) {
    return (
      <div
        data-testid={testid}
        data-avatar-mode={mode}
        className={cn("flex shrink-0 items-center justify-center overflow-hidden rounded-lg", className ?? "h-8 w-8")}
        {...rest}
      >
        {/* The brand image has a pure white background (1024×1024); add bg-white so no gray edge shows in dark theme */}
        <img src={brandSrc} alt={name || id || ""} className="h-full w-full bg-white object-cover" loading="lazy" />
      </div>
    );
  }

  return (
    <div
      data-testid={testid}
      data-avatar-mode="initials"
      title={name || id}
      aria-label={name || id}
      className={cn(
        "flex shrink-0 select-none items-center justify-center rounded-lg font-semibold",
        PALETTE[hashIndex(key, PALETTE.length)],
        className ?? "h-8 w-8",
        textClassName ?? "text-xs"
      )}
      {...rest}
    >
      {label}
    </div>
  );
}
