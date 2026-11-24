/**
 * User-facing text for API errors (mapped from stable BFF codes)
 * ─────────────────────────────────────────────────────────────
 * Why this layer exists (plan §8-⑤, decided: map by code):
 *   The backend `message` is written for whoever debugs — it carries field names,
 *   MCP tool names and SQL fragments. Showing it verbatim to a user produces
 *   "agx_list_by_network failed", which reads as a bug.
 *   `code` is the stable contract → render human text; **unknown codes always fall
 *   back to the backend message**, because hiding the original error makes
 *   debugging impossible.
 *
 * Rule for adding codes: only when different causes need different wording.
 * A mere rephrasing should fall through to the backend message instead.
 */
export default {
  generic: "Something went wrong. Please try again.",
  network: "Can't reach the service (backend down or network lost)",
  unauthenticated: "Your session expired. Please sign in again.",
  invalidCredentials: "Wrong username or password",
  permissionDenied: "You don't have permission to do that",
  notFound: "Not found — it may have been deleted",
  invalidField: "Invalid value in the submitted form",
  noValidFields: "Nothing to update",
  mcpUnavailable: "Kernel channel unavailable — try again shortly",
  mcpToolError: "The kernel failed while running this",
  kernelUnavailable: "Kernel database unavailable",
  stateMismatch: "The state changed. Refresh and try again.",
  conflict: "Conflicts with the current state",
  badRequest: "Bad request parameters",
  serverError: "Internal server error",
  audioTooLarge: "Recording too long — please record in segments",
  emptyAudio: "No audio was captured",
  asrHostNotFound: "Speech recognition isn't configured",
  taskNotFound: "Task not found",
  cmdNotFound: "Quick command not found",
  sessionNotOwned: "This session doesn't belong to the current agent",
  replyRequired: "This task requires a reply — please fill in the conclusion",
} as const;
