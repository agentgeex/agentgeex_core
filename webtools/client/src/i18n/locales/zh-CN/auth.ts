/** Login page (/login, outside AppShell) */
export default {
  title: "登录到控制台",
  subtitle: "输入账号以继续",
  username: "用户名",
  password: "密码",
  usernamePlaceholder: "operator",
  submit: "登录",
  submitting: "登录中…",
  needBoth: "请输入用户名和密码",
  invalid: "凭证无效，请重新登录",
  dataFailed: "已登录，但读取数据失败：{{message}}",
  failed: "登录失败，请检查后端服务",
  ttlHint: "登录有效期 30 天，过期后需重新登录",
} as const;
