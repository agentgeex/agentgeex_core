/**
 * Short copy for miscellaneous components (platform labels / empty member state / source dir / deep-link errors /
 * agent status words)
 * ★ Role names are not here — the single site-wide definition is in locales/{lang}/roles.ts
 * ★ They live in one file because each is only 1-2 words; a file per entry would be harder to find.
 */
export default {
  platform: {
    openclawDesc: "OpenClaw 运行时",
    custom: "自定义",
    customDesc: "自定义运行时",
    unknown: "未知",
    unknownDesc: "未标注平台",
    unknownPlatform: "未知平台：{{type}}",
    clickOpen: "点击打开",
  },
  role: {
    none: "暂无成员。",
  },
  sourceDir: {
    copyFailed: "复制失败（浏览器未授权剪贴板）",
    openedLocally: "已在本机打开",
    unset: "未设置源码目录",
    unsetHint: "（在「编辑」里填 source_dir 后，这里会出现打开入口）",
    openTitle: "在 BFF 所在主机的文件管理器中打开（本机访问才生效）",
    open: "打开文件夹",
    copyPath: "复制路径",
    copied: "已复制",
  },
  deepLink: {
    missingHost: "缺少 host_id",
    hostNotFound: "主机 {{host}} 不在资产表中",
    platformNotConfigured: "主机 {{host}} 未配置 {{platform}} 平台的访问方式",
    containerNoAddr: "主机 {{host}} 的 {{platform}} 是 container 模式但没配 addr",
    processNoPort: "主机 {{host}} 的 {{platform}} 是 process 模式但没配 port",
    missingEndpointIp: "主机 {{host}} 缺少 endpoint_ip，且 {{platform}} 未配 addr",
    loopbackUnreachable:
      "主机 {{host}} 的 {{platform}} 只配了回环地址 {{ip}}，从本页面（{{page}}）不可达；请为该平台配 addr（IP 或 .local 主机名）",
  },
  agentState: {
    template: "模板",
    offline: "已下线",
    unregisteredProject: "未登记项目",
    boardApiError: "看板接口返回异常",
    notLoggedIn: "尚未登录：本地无访问凭证",
    sessionExpired: "登录已过期，请重新登录",
  },
} as const;
