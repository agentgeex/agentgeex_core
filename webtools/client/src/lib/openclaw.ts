/**
 * lib/openclaw.ts — OpenClaw console deep links
 * ────────────────────────────────────────────────────────────────
 * [2026-10-26 Feng]
 *   Background: OpenClaw's own session search is not usable (cannot find an agent), so in the console
 *         every agent gets a link that "goes straight to its framework page".
 *   Rule: `http://{host.endpoint_ip}:18789/chat/{agent_id}`
 *         - host comes from `agx_host_inventory.endpoint_ip` (the `ip` in the hosts snapshot)
 *         - when no IP is available, fall back to 127.0.0.1 (same machine)
 *   Limited to: **openclaw platform only** (`agent_type === "openclaw"`).
 *         Other platforms (dsh / codex / claude_code / custom) have different paths, no guessing --
 *         the decision stays here; adding a new platform later just adds one line to `PLATFORM_CHAT_PATH`.
 */
import { listAgents, listHosts } from "@/providers/agentgeex";
import type { HostRow, HostPlatform } from "@/mock/agentgeex";
import i18n from "@/i18n";

/**
 * Platform -> session deep-link path template (`{agent}` is replaced with agent_id).
 * Only "known and verified" platforms are listed; unlisted platforms never generate a link (no invented paths).
 *
 * ★ 2026-11-04: **paths only** remain here, port/address all come from
 *   `agx_host_inventory.platforms` (see resolvePlatformEndpoint).
 *   The old version had an `OPENCLAW_PORT = 18789` constant concatenated into the URL --
 *   a port hardcoded in the frontend meant changing it required a code change and rebuild, and it assumed
 *   "all hosts use the same port" (in reality host 188 runs both the 18789 and 18799 instances).
 */
const PLATFORM_CHAT_PATH: Record<string, string> = {
  // Measured 200 on 2026-10-26: http://127.0.0.1:18789/chat/agx_ui_lead
  openclaw: `/chat/{agent}`,
};

/** Whether this platform supports "jump to framework page" */
export function supportsChatDeepLink(agentType?: string): boolean {
  return !!PLATFORM_CHAT_PATH[(agentType || "").trim().toLowerCase()];
}

/**
 * host_id -> endpoint_ip.
 * ★ No module-level cache: there are only a few hosts, each lookup is an array scan (nanoseconds);
 *   and a cache could leave an empty table before hydrate completes / after a retry -> silently degrading to
 *   127.0.0.1 (hard to diagnose).
 */
function hostIpOfHost(hostId: string): string {
  const h = listHosts().find((x) => x.id === hostId);
  return h?.ip || "";
}

/** Get a host's IP; unknown/empty -> fall back to 127.0.0.1 (same machine) */
export function hostIpOf(hostId?: string): string {
  if (!hostId) return "127.0.0.1";
  return hostIpOfHost(hostId) || "127.0.0.1";
}

/* ────────────────────────────────────────────────────────────────
 * Platform endpoint resolution (2026-11-04 Feng)
 *
 * Requirement in his words: "Since the platform is bound, the address/endpoint of this platform can
 *   actually be configured too... keep it all in this one place with one change"
 *   "Mind the isolation issue, docker for instance actually has its own host. Having its own host and being
 *   the same process are different things"
 *
 * So addresses are **no longer derived from code constants**, always read the `platforms` (JSON array) on
 * the host row:
 *   { platform, name?, mode, port?, addr?, base_path?, note? }
 *
 * `mode` is the only reason this structure exists:
 *   - process   same-host process -> address = endpoint_ip + port
 *   - container container/isolated netns -> connect via addr directly, **never** build from endpoint_ip
 *
 * Not found returns ok:false -- better no entry in the UI than a link pointing at the wrong machine.
 * ──────────────────────────────────────────────────────────────── */

/** Loopback address set (from another machine these mean "itself", not usable across machines) */
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "0.0.0.0"]);

function isLoopback(host: string): boolean {
  return LOOPBACK_HOSTS.has((host || "").trim().toLowerCase());
}

/** Hostname of the current page (used for loopback substitution in same-host deployment). Returns an empty string under SSR/tests. */
function pageHostname(): string {
  if (typeof window === "undefined") return "";
  return (window.location?.hostname || "").trim();
}

export interface PlatformEndpoint {
  /** Whether resolution succeeded; on false origin/basePath are empty strings, see reason for why */
  ok: boolean;
  /** scheme + host + port, e.g. http://192.168.0.188:18789 */
  origin: string;
  /** Session page path prefix, e.g. /chat (no trailing slash) */
  basePath: string;
  /** Failure reason (usable directly as tooltip text) */
  reason: string;
}

const FAIL = (reason: string): PlatformEndpoint => ({ ok: false, origin: "", basePath: "", reason });

/**
 * Resolve the reachable address of "a platform on a host".
 *
 * @param hostId     host ID (agx_agent_registry.host_id)
 * @param platform   platform identifier (same set as agent_type)
 * @param callerHost hostname of the caller; if omitted, reads window.location.hostname
 *                   (passing it explicitly is only for testability)
 */
export function resolvePlatformEndpoint(
  hostId: string | undefined,
  platform: string,
  callerHost?: string,
): PlatformEndpoint {
  const pid = (hostId || "").trim();
  if (!pid) return FAIL(i18n.t("miscUi.deepLink.missingHost"));

  const host = listHosts().find((h: HostRow) => h.id === pid) as HostRow | undefined;
  if (!host) return FAIL(i18n.t("miscUi.deepLink.hostNotFound", { host: pid }));

  const list = host.platforms || [];
  const p = list.find((x: HostPlatform) => (x.platform || "").trim().toLowerCase() === platform);
  if (!p) {
    return FAIL(i18n.t("miscUi.deepLink.platformNotConfigured", { host: pid, platform }));
  }

  const tpl = PLATFORM_CHAT_PATH[platform] || "";
  const tplPath = tpl.replace("{agent}", ""); // "/chat/" -> used as basePath fallback
  const basePath = (p.base_path || tplPath || "").replace(/\/+$/, "");

  // ── Container: address is self-contained, use it directly ────────────────────────────────
  if (p.mode === "container") {
    const addr = (p.addr || "").trim();
    if (!addr) {
      return FAIL(i18n.t("miscUi.deepLink.containerNoAddr", { host: pid, platform }));
    }
    const origin = /^https?:\/\//i.test(addr) ? addr.replace(/\/+$/, "") : `http://${addr}`;
    return { ok: true, origin, basePath, reason: "" };
  }

  // ── Process: address + port ────────────────────────────────────
  //   addr is optional: if given, use it as the "host part", overriding endpoint_ip.
  //   Why needed: endpoint_ip is the host's default address, but different platforms on the same host
  //   may be exposed on different NICs/hostnames; and for hosts whose endpoint_ip=127.0.0.1 (e.g. dev machines)
  //   it is simply unreachable across machines -- writing addr there is safer than changing the existing endpoint_ip (no effect on other readers).
  const port = Number(p.port || 0);
  if (!port) {
    return FAIL(i18n.t("miscUi.deepLink.processNoPort", { host: pid, platform }));
  }

  const explicit = (p.addr || "").trim();
  let ip = explicit || (host.ip || "").trim();
  if (!ip) {
    return FAIL(i18n.t("miscUi.deepLink.missingEndpointIp", { host: pid, platform }));
  }

  // ★ Loopback addresses do not work across machines: opening the page from another machine, 127.0.0.1 points to **the browser's machine**.
  //   this page is also on loopback -> same-machine dev, connect directly (correct).
  //   this page is not on loopback -> **do not generate a link** (return failure).
  //   Never substitute the page hostname -- that assumes "webtools and the target platform are on the same machine",
  //   and once webtools is deployed on another machine (e.g. a unified console) it builds a link to the wrong machine.
  //   The correct approach: configure a reachable addr (IP or hostname) for that host/platform.
  if (isLoopback(ip)) {
    const ph = (callerHost ?? pageHostname()).trim();
    if (ph && !isLoopback(ph)) {
      return FAIL(
        i18n.t("miscUi.deepLink.loopbackUnreachable", {
          host: pid, platform, ip, page: ph,
        }),
      );
    }
  }

  return { ok: true, origin: `http://${ip}:${port}`, basePath, reason: "" };
}

/**
 * Build the framework page link of an Agent (= that Agent's main session).
 * Platform unsupported / missing agent_id / the host has no address configured for this platform -> null
 * (the caller does not render the entry based on that).
 *
 * When `sessionSlug` is non-empty, one more segment is appended -> `/chat/{agent}/{slug}` (see sessionDeepLink).
 *
 * ★ 2026-11-04: the address is no longer `hostIpOf() + OPENCLAW_PORT`, it comes from
 *   `resolvePlatformEndpoint` reading the platforms config on the host row.
 *   If not found return null -- the old code fell back to 127.0.0.1, which across machines points at the
 *   browser itself: a link that looks real but does not open, worse than none.
 */
export function chatDeepLink(
  agentId?: string,
  agentType?: string,
  hostId?: string,
  sessionSlug?: string,
): string | null {
  const id = (agentId || "").trim();
  if (!id) return null;
  const type = (agentType || "").trim().toLowerCase();
  if (!PLATFORM_CHAT_PATH[type]) return null;

  const ep = resolvePlatformEndpoint(hostId, type);
  if (!ep.ok) return null;

  const slug = (sessionSlug || "").trim();
  const path =
    `${ep.basePath}/${encodeURIComponent(id)}` + (slug ? `/${encodeURIComponent(slug)}` : "");
  return `${ep.origin}${path}`;
}

/* ────────────────────────────────────────────────────────────────
 * Session deep link (2026-10-28 Feng)
 *
 * Requirement in his words: "A session has an id, and the session id corresponds to the UI. It used to be
 * appended directly after the UI, now there is a transformation". His example:
 *   http://192.168.0.188:18789/chat/book_pydev_850472/book_code_engineer
 * while the matching session id in the DB is:
 *   agent:book_pydev_850472:book_code_engineer
 *
 * i.e. the mapping rule (the only place; change here and the whole site follows):
 *   `agent:{agent_id}:{slug}`  ->  `/chat/{agent_id}/{slug}`
 *
 * ★ Verified against OpenClaw source (control-ui boot): the /chat route parses the path tail into
 *   agentPath + sessionPath, and `/chat/<agent>/<session>` is exactly the shape it accepts.
 * ★ `main` session special case: the main session is the agent's default chat page, so use `/chat/{agent}`
 *   (do not append `/main`, avoiding landing on a session name that does not exist).
 * ──────────────────────────────────────────────────────────────── */

export interface ParsedSessionId {
  /** agent id (= first segment of `/chat/`) */
  agentId: string;
  /** session short name (= second segment of `/chat/`); `main` means the main session */
  slug: string;
}

/**
 * Parse session id `agent:{agent_id}:{slug}`.
 * Anything not matching that shape (empty, old format, someone else's session key) -> null.
 */
export function parseSessionId(sessionId?: string): ParsedSessionId | null {
  const s = (sessionId || "").trim();
  if (!s.startsWith("agent:")) return null;
  const rest = s.slice("agent:".length);
  const i = rest.indexOf(":");
  if (i <= 0) return null; // `agent:xxx` has no session segment -> not a session id
  const agentId = rest.slice(0, i);
  const slug = rest.slice(i + 1);
  if (!agentId || !slug) return null;
  return { agentId, slug };
}

/**
 * agent_id -> its platform type and host (a session row only has agent_id; the deep link needs both).
 * `found=0` means this agent is not in the registry -- the host is unknown, so
 * **no link is generated** (better no entry than a broken link pointing at local 127.0.0.1).
 */
export function agentLinkMeta(agentId?: string): {
  found: boolean;
  agentType: string;
  hostId: string;
} {
  const id = (agentId || "").trim();
  if (!id) return { found: false, agentType: "", hostId: "" };
  const a = listAgents().find((x) => x.id === id);
  if (!a) return { found: false, agentType: "", hostId: "" };
  return { found: true, agentType: a.agentType || "openclaw", hostId: a.host_id || "" };
}

/**
 * Session deep link: `/chat/{agent}/{slug}`.
 *
 * - session id malformed -> null (no invented URLs, the caller does not render an entry)
 * - `slug === "main"` -> degrade to the agent's default chat page `/chat/{agent}`
 * - `agentType` / `hostId` not passed -> look them up live from agent_id (session rows only have agent_id)
 */
export function sessionDeepLink(
  sessionId?: string,
  agentType?: string,
  hostId?: string,
): string | null {
  const parsed = parseSessionId(sessionId);
  if (!parsed) return null;
  const meta = agentLinkMeta(parsed.agentId);
  // Bypassing the registry is allowed only when the caller explicitly passes agentType+hostId; otherwise no link if the agent is not in the DB
  if (!meta.found && !(agentType && hostId)) return null;
  const type = agentType || meta.agentType;
  const host = hostId || meta.hostId;
  if (!supportsChatDeepLink(type)) return null;
  const slug = parsed.slug === "main" ? "" : parsed.slug;
  return chatDeepLink(parsed.agentId, type, host, slug);
}
