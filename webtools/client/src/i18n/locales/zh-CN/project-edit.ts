/**
 * Project edit dialog (row action "edit")
 */
export default {
  title: "编辑项目",
  kernelHint: "写入走内核 agx_update_project（字段分级与归属校验在内核）",
  fName: "项目名称",
  fAbbr: "简称",
  fAbbrHint: "全小写字母，用于 Agent 命名前缀",
  fLogo: "Logo",
  fLogoHint: "URL 或 /brand/ 下本地路径；留空则首字母兜底",
  fSourceDir: "源码目录",
  fXdesc: "项目说明书",
  fXdescHint: "Markdown；Agent 启动时读取的就是这份（agx_project.x_desc）",
  fActor: "操作身份",
  fActorHint: "内核按此判断权限（需 L5：project_manager / workshop_manager）",
  actorPlaceholder: "选择操作身份",
  errNameEmpty: "项目名称不能为空",
  errNoChanges: "没有任何修改",
  errActorMissing: "请选择操作身份（需 project_manager / workshop_manager 角色）",
  updated: "项目已更新",
  cancel: "取消",
  save: "保存",
} as const;
