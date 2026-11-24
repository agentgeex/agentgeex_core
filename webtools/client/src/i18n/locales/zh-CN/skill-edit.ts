/**
 * Skill edit page (/skills/:id/edit)
 */
export default {
  title: "编辑技能",
  notFound: "技能 {{id}} 不在当前快照里，可能已被移除或尚未同步。",
  subtitle: "{{id}} · 贡献者 {{name}}",
  kernelHint: "写入走内核 agx_skill_contribute（按 skill_id 更新，幂等；缓存同步在内核）",
  fName: "技能名",
  fNameHint: "内核按名字做幂等键，改名等于换一条身份",
  fCategory: "分类",
  fCategoryHint: "如 browser / github / search / mcp / other",
  fDesc: "一句话说明",
  fDescHint: "别人在列表里靠这句决定要不要点开",
  fTags: "标签",
  fTagsHint: "逗号分隔",
  contentLabel: "正文（Markdown）",
  contentHint: "判据：读了能照做 → 放这里；只说明「谁干了啥」 → 那是公告，不该进技能库。",
  updated: "技能已更新",
  errNameEmpty: "技能名不能为空（内核按名字做幂等，空名会造出垃圾数据）",
  errContentEmpty: "技能正文不能为空",
  footerHint: "取用次数 / 有用票不在此页修改（那是使用信号，不是描述）",
} as const;
