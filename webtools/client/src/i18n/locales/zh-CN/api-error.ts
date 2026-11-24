/**
 * User-facing copy for API errors (mapped from the stable code returned by the BFF)
 * ─────────────────────────────────────────────────────────────
 * Why this layer exists (plan §8-⑤ decided "map by code on the front end"):
 *   the backend message is written for **whoever debugs it** (it contains field names, MCP tool names, SQL fragments),
 *   throwing it at a user produces incomprehensible text like "agx_list_by_network failed".
 *   code is a stable contract → human wording by code; **unknown codes always fall back to the backend message**,
 *   better ugly than pretending to know (swallowing the original error makes debugging hopeless).
 *
 * Rule for adding a code: only add ones where "different cause → different handling";
 * do not add mere rewordings, let them fall back to the backend text and avoid maintaining two copies.
 */
export default {
  generic: "操作失败，请重试",
  network: "连不上服务（后端未启动或网络中断）",
  unauthenticated: "登录已失效，请重新登录",
  invalidCredentials: "用户名或密码错误",
  permissionDenied: "权限不足",
  notFound: "目标不存在或已被删除",
  invalidField: "提交的内容不合法",
  noValidFields: "没有任何可更新的字段",
  mcpUnavailable: "内核通道不可用，请稍后重试",
  mcpToolError: "内核执行时出错",
  kernelUnavailable: "内核数据库不可用",
  stateMismatch: "状态已变化，请刷新后重试",
  conflict: "与当前状态冲突",
  badRequest: "请求参数有误",
  serverError: "服务器内部错误",
  audioTooLarge: "录音太长，请分段录制",
  emptyAudio: "没有录到声音",
  asrHostNotFound: "语音识别服务未配置",
  taskNotFound: "任务不存在",
  cmdNotFound: "快捷指令不存在",
  sessionNotOwned: "这个会话不属于当前 Agent",
  replyRequired: "该任务要求回执，请填写结论",
} as const;
