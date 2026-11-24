/**
 * Plan list page (/plans)
 * ★ Status words are not here — they use the global `status.*` (same set as the dashboard and executions).
 */
export default {
  colName: "计划名称",
  colProject: "所属项目",
  colOwner: "负责人",
  colTrigger: "触发",
  colStatus: "状态",
  colDone: "完成度",
  colNextRun: "下次执行",
  searchPlaceholder: "搜索计划 / 项目 / 负责人…",
  fProject: "项目",
  fOwner: "负责人",
  fStatus: "状态",
  flowDialogTitle: "计划流程图",
} as const;
