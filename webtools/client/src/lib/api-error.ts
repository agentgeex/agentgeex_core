/**
 * API error → user-readable copy (the single entry point on the site)
 * ─────────────────────────────────────────────────────────────
 * Plan §8-⑤ decided: **map by code on the front end**, while pushing the BFF to make codes a stable enum.
 *
 * Three-level fallback, order matters:
 *   ① known code        → language pack `apiError.*` (human wording)
 *   ② unknown code + message → backend text as-is (ugly is better than pretending to know the cause)
 *   ③ neither           → one generic sentence by HTTP status
 *
 * Why not just build `t(`apiError.${code}`)`: codes are upper case like `NOT_FOUND`, and when i18next
 * cannot find them it **returns the key verbatim**, so the UI shows "apiError.NOT_FOUND".
 * A whitelist lookup is mandatory: translate only on a hit.
 */
import type { TFunction } from "i18next";

export interface ApiErrorLike {
  code?: string;
  status?: number;
  message?: string;
  details?: unknown;
}

/** code → language pack key. Only "different cause needs different wording" entries; everything else falls back to the backend text. */
const CODE_KEY: Record<string, string> = {
  BFF_UNREACHABLE: "apiError.network",
  UNAUTHENTICATED: "apiError.unauthenticated",
  INVALID_CREDENTIALS: "apiError.invalidCredentials",
  PERMISSION_DENIED: "apiError.permissionDenied",
  NOT_FOUND: "apiError.notFound",
  INVALID_FIELD: "apiError.invalidField",
  NO_VALID_FIELDS: "apiError.noValidFields",
  MCP_UNAVAILABLE: "apiError.mcpUnavailable",
  MCP_TOOL_ERROR: "apiError.mcpToolError",
  KERNEL_UNAVAILABLE: "apiError.kernelUnavailable",
  KERNEL_ERROR: "apiError.kernelUnavailable",
  STATE_MISMATCH: "apiError.stateMismatch",
  CONFLICT: "apiError.conflict",
  BAD_REQUEST: "apiError.badRequest",
  AUDIO_TOO_LARGE: "apiError.audioTooLarge",
  EMPTY_AUDIO: "apiError.emptyAudio",
  ASR_HOST_NOT_FOUND: "apiError.asrHostNotFound",
  TASK_NOT_FOUND: "apiError.taskNotFound",
  CMD_NOT_FOUND: "apiError.cmdNotFound",
  SESSION_NOT_OWNED: "apiError.sessionNotOwned",
  REPLY_REQUIRED: "apiError.replyRequired",
};

/** A few callers need to know whether the code was recognized, to decide whether to add their own page-level hint */
export function isKnownApiCode(code?: string): boolean {
  return !!code && !!CODE_KEY[code];
}

/** Turn whatever was caught into one human sentence. */
export function apiErrorText(e: unknown, t: TFunction): string {
  if (typeof e === "string" && e.trim()) return e;
  if (!e || typeof e !== "object") return t("apiError.generic");

  const err = e as ApiErrorLike;

  if (err.code && CODE_KEY[err.code]) return t(CODE_KEY[err.code]);
  /* Network-layer error: fetch throws a TypeError directly, status is 0 */
  if (err.status === 0) return t("apiError.network");

  if (err.message && err.message.trim()) return err.message;

  switch (err.status) {
    case 400:
      return t("apiError.badRequest");
    case 401:
      return t("apiError.unauthenticated");
    case 403:
      return t("apiError.permissionDenied");
    case 404:
      return t("apiError.notFound");
    case 409:
      return t("apiError.conflict");
    default:
      return typeof err.status === "number" && err.status >= 500
        ? t("apiError.serverError")
        : t("apiError.generic");
  }
}
