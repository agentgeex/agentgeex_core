/**
 * Small UI strings (platform tags / empty members / source dir / deep-link errors / agent states)
 * Role names live in locales/en-US/roles.ts — the single source.
 */
export default {
  platform: {
    openclawDesc: "OpenClaw runtime",
    custom: "Custom",
    customDesc: "Custom runtime",
    unknown: "Unknown",
    unknownDesc: "Platform not recorded",
    unknownPlatform: "Unknown platform: {{type}}",
    clickOpen: "click to open",
  },
  role: {
    none: "No members yet.",
  },
  sourceDir: {
    copyFailed: "Copy failed (the browser blocked clipboard access)",
    openedLocally: "Opened on this machine",
    unset: "No source directory set",
    unsetHint: "(fill in source_dir under Edit and an open link appears here)",
    openTitle: "Open in the file manager of the BFF host (only works when browsing locally)",
    open: "Open folder",
    copyPath: "Copy path",
    copied: "Copied",
  },
  deepLink: {
    missingHost: "host_id is missing",
    hostNotFound: "Host {{host}} is not in the inventory",
    platformNotConfigured: "Host {{host}} has no access configuration for {{platform}}",
    containerNoAddr: "Host {{host}}: {{platform}} runs in container mode but has no addr",
    processNoPort: "Host {{host}}: {{platform}} runs in process mode but has no port",
    missingEndpointIp: "Host {{host}} has no endpoint_ip and {{platform}} has no addr",
    loopbackUnreachable:
      "Host {{host}} only has the loopback address {{ip}} for {{platform}}, unreachable from this page ({{page}}); configure an addr (IP or .local hostname) for that platform",
  },
  agentState: {
    template: "Template",
    offline: "Offline",
    unregisteredProject: "Unregistered project",
    boardApiError: "The dashboard API returned an unexpected response",
    notLoggedIn: "Not signed in: no local credentials",
    sessionExpired: "Your session has expired — please sign in again",
  },
} as const;
