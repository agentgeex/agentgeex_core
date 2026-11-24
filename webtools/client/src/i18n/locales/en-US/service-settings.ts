/**
 * Service settings dialog (ASR hosts / MCP connection)
 */
export default {
  available: "Available",
  unreachable: "Unreachable",
  untested: "Not probed yet",
  probeHint: "Click to probe this host now (one WS handshake)",
  probing: "Probing",
  justNow: "just now",
  secondsAgo: "{{n}}s ago",
  minutesAgo: "{{n}}m ago",
  hoursAgo: "{{n}}h ago",
  title: "Service settings",
  tabAsr: "Speech recognition",
  tabMcp: "MCP connection",

  asrPickTitle: "This chooses which service this machine uses — it is not a deployment.",
  /* Trailing spaces are deliberate: these fragments wrap <code>/<strong> elements. */
  asrSourceA: "The selectable hosts come from ",
  asrSourceB: " in ",
  asrSourceC: " on the server (add or remove machines there and restart); the choice here takes effect ",
  asrSourceStrong: "immediately",
  asrSourceD: " and survives restarts.",
  loadingHosts: "Loading host list…",
  hostCount: "{{count}} available",
  current: "Current: ",
  inUse: "In use",
  checkedAt: "checked {{when}}",
  autoProbe: "The backend probes every host every 8 seconds, so \"Available\" updates by itself — no need to keep clicking. The small button on the right only means \"tell me right now\".",
  failLoud: "If the selected host is unreachable, recording reports an explicit error (it never silently turns into empty text) — that kind of failure is the hardest to diagnose.",
  reload: "Reload",
} as const;
