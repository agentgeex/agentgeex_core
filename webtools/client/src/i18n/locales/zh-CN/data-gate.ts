/**
 * Data gate (whole-page fallback when the backend is unreachable / not signed in)
 */
export default {
  unreachable: "后端服务未启动或网络不可达",
  forbidden: "当前账号无权限读取数据",
  serverError: "后端处理异常（HTTP {{status}}）",
  kernelError: "后端已连接，但数据库/缓存读取失败",
  unknown: "未知原因",
  title: "暂时无法连接后端",
  body: "页面已停止加载，避免展示不真实的数据。",
  reasonPrefix: "原因：",
  retrying: "重试中…",
  retry: "重试连接",
  relogin: "重新登录",
  notLoggedIn: "尚未登录 — 可点「重新登录」进入登录页",
} as const;
