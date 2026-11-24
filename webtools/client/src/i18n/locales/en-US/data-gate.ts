/**
 * Data gate (full-page fallback when the backend is unreachable or the user is not signed in)
 */
export default {
  unreachable: "The backend is not running or the network is unreachable",
  forbidden: "This account is not allowed to read data",
  serverError: "The backend failed (HTTP {{status}})",
  kernelError: "Backend reachable, but database/cache reads failed",
  unknown: "Unknown reason",
  title: "Cannot reach the backend right now",
  body: "Loading has been stopped so that no inaccurate data is shown.",
  reasonPrefix: "Reason: ",
  retrying: "Retrying…",
  retry: "Retry connection",
  relogin: "Sign in again",
  notLoggedIn: "Not signed in — use \"Sign in again\" to open the login page",
} as const;
