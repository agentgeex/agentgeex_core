/**
 * Notices / work log list page (/notices)
 * ★ type / visibility values are **stored values** (log/decision/…, private/project/network);
 *   translate the label only, never the value — changing it breaks saved filters.
 */
export default {
  colType: "类型",
  colTitle: "标题",
  colProject: "项目",
  colVisibility: "可见性",
  colTime: "时间",
  untitled: "（无标题）",
  searchPlaceholder: "搜索标题 / 正文 / Agent / 项目…",
  fType: "类型",
  fProject: "项目",
  empty: "暂无公告",
  type: {
    log: "日志",
    decision: "决策",
    error: "踩坑",
    handoff: "交接",
    config: "配置",
    other: "其他",
  },
  visibility: {
    private: "私有",
    project: "项目",
    network: "全网",
  },
} as const;
