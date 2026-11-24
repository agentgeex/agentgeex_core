/**
 * Platform Logo
 * ────────────────────────────────────────────────────────────────
 * Data source: `agx_agent_registry.agent_type` (exposed by the BFF as `agentType`).
 *   Values: openclaw | dsh | codex | claude_code | custom (see the table comment + actual distribution)
 *
 * Each value maps to one recognition icon (stored in `public/logos/`, see that directory's README.md).
 * The frontend always renders through this component; bare `<img>`/paths elsewhere are forbidden --
 * keeps the convention consistent.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { Bot, Cpu, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PlatformMeta {
  /** Localized / official display name */
  label: string;
  /** logo path (under public, absolute; no img rendered when missing) */
  logo?: string;
  /** One-line description (tooltip) */
  desc?: string;
}

/**
 * Platform -> icon/name mapping.
 * ★ Unlisted values use the fallback (no crash, no invented logo): show the raw value + a generic icon.
 */
export const PLATFORM_META: Record<string, PlatformMeta> = {
  /* desc uses i18n keys (brand-name labels are not translated). platformMeta() resolves keys into text. */
  openclaw: { label: "OpenClaw", logo: "/logos/openclaw.svg", desc: "miscUi.platform.openclawDesc" },
  /* [2026-11-16 11:29 Feng] "DSH is a blue icon, right? Why did you make it black and white now"
     -- it pointed at `dsh.svg`, and that file is the **monochrome black whale mark** (`dsh-dark.svg` is the
        same whale in white, for dark backgrounds), so on a light card it is just a black blob.
     Switched to the brand blue whale mark (`deepseek.svg`, fill #5786FE -- the official brand color;
     DSH = DeepSeek Harness, the mark is the DeepSeek whale).
     ★ Do not delete the other two files: `dsh.svg`/`dsh-dark.svg` are the black/white monochrome versions
       of the official Web UI favicon, needed as a pair when building "dark-theme adaptation" later;
       delete them and there is nothing to swap in. */
  dsh: { label: "DSH", logo: "/logos/deepseek.svg", desc: "DeepSeek Harness" },
  codex: { label: "Codex", logo: "/logos/codex.svg", desc: "OpenAI Codex" },
  claude_code: { label: "Claude Code", logo: "/logos/claude-code.svg", desc: "Anthropic Claude Code" },
  custom: { label: "miscUi.platform.custom", desc: "miscUi.platform.customDesc" },
};

/** Get platform meta (unknown value -> fallback entry, label=raw value).
 *  ★ A pure function cannot use hooks -> read the i18n singleton directly; switching language reloads the
 *    whole page, so no subscription is needed here. */
export function platformMeta(type?: string): PlatformMeta {
  const key = (type || "").trim().toLowerCase();
  const tr = (k: string, o?: Record<string, unknown>) => i18n.t(k, o);
  const entry = PLATFORM_META[key];
  if (entry) {
    /* label / desc may both be keys (custom / description), translate once uniformly; unresolvable keys are returned as-is */
    const label = tr(entry.label);
    return { ...entry, label, desc: entry.desc ? tr(entry.desc) : entry.desc };
  }
  if (!key) return { label: tr("miscUi.platform.unknown"), desc: tr("miscUi.platform.unknownDesc") };
  return { label: type!, desc: tr("miscUi.platform.unknownPlatform", { type }) };
}

/** Platform display name */
export const platformLabel = (type?: string) => platformMeta(type).label;

/**
 * Platform icon.
 * With a logo render the image; without (custom/unknown) -> generic icon fallback.
 */
export function PlatformLogo({
  type, className, imgClassName, title, href,
}: {
  type?: string;
  /** Frame size (e.g. "h-8 w-8") */
  className?: string;
  /** Image size (e.g. "h-4 w-4") */
  imgClassName?: string;
  title?: string;
  /**
   * [2026-10-27 Feng] With a value -> render as a clickable external link (straight to that platform's web page).
   * The icon itself is the entry, no separate "Open OpenClaw" button -- saves space.
   * ★ Must stopPropagation: the whole card/table row is clickable (into the detail),
   *   without stopping it clicking the icon also navigates and bounces the user back inside the site.
   */
  href?: string | null;
}) {
  const { t } = useTranslation();
  const meta = platformMeta(type);
  const clickable = !!href;
  const tip = title ?? `${meta.label}${meta.desc ? ` · ${meta.desc}` : ""}${clickable ? ` · ${t("miscUi.platform.clickOpen")}` : ""}`;
  const inner = meta.logo ? (
    <img
      src={meta.logo}
      alt={meta.label}
      className={cn("object-contain", imgClassName ?? "h-5 w-5")}
      loading="lazy"
    />
  ) : (
    type === "custom"
      ? <Cpu className={cn("text-muted-foreground", imgClassName ?? "h-5 w-5")} />
      : <Bot className={cn("text-foreground/80", imgClassName ?? "h-5 w-5")} />
  );

  const cls = cn(
    // Neutral background + thin border: does not compete with the brand logo (the old sky->fuchsia gradient used a raw palette and covered the logo's own color)
    "flex shrink-0 items-center justify-center rounded-lg bg-background ring-1 ring-border",
    className ?? "h-9 w-9",
    clickable &&
      "cursor-pointer transition-all hover:ring-2 hover:ring-primary hover:bg-accent"
  );
  const shared = {
    "data-testid": `platform-logo-${(type || "unknown").toLowerCase()}`,
    "data-platform": type || "",
    "data-clickable": clickable ? "1" : undefined,
    title: tip,
    "aria-label": tip,
  } as const;

  if (clickable) {
    return (
      <a
        {...shared}
        href={href!}
        target="_blank"
        rel="noreferrer noopener"
        onClick={(e) => e.stopPropagation()}
        className={cls}
      >
        {inner}
      </a>
    );
  }
  return <div {...shared} className={cls}>{inner}</div>;
}

/** Platform chip (icon + name), used in tables/tag areas */
export function PlatformTag({
  type, className, href, ...rest
}: {
  type?: string;
  className?: string;
  /** With a value -> the whole block becomes an external link (2026-10-27: used on the detail page to replace the "Open OpenClaw" button) */
  href?: string | null;
} & React.HTMLAttributes<HTMLSpanElement>) {
  const { t } = useTranslation();
  const meta = platformMeta(type);
  const clickable = !!href;
  const body = (
    <>
      {meta.logo
        ? <img src={meta.logo} alt={meta.label} className="h-4 w-4 object-contain" loading="lazy" />
        : <Bot className="h-4 w-4" />}
      {meta.label}
      {/* Small external-link arrow that only appears when clickable: makes "this badge is clickable" visible at a glance without taking a button's space */}
      {clickable && <ExternalLink className="h-3 w-3 opacity-70" />}
    </>
  );
  const cls = cn(
    "inline-flex items-center gap-1.5 text-xs text-muted-foreground",
    clickable &&
      "cursor-pointer rounded px-1.5 py-0.5 -mx-1.5 text-foreground/85 transition-colors hover:bg-accent hover:text-primary",
    className
  );

  if (clickable) {
    return (
      <a
        data-testid={`platform-tag-${(type || "unknown").toLowerCase()}`}
        data-clickable="1"
        {...rest}
        href={href!}
        target="_blank"
        rel="noreferrer noopener"
        onClick={(e) => e.stopPropagation()}
        title={`${meta.label}${meta.desc ? ` · ${meta.desc}` : ""} · ${t("miscUi.platform.clickOpen")}`}
        className={cls}
      >
        {body}
      </a>
    );
  }
  return (
    <span
      data-testid={`platform-tag-${(type || "unknown").toLowerCase()}`}
      title={meta.desc}
      className={cls}
      {...rest}
    >
      {body}
    </span>
  );
}
