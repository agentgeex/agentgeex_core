/**
 * Skill edit page (/skills/:id/edit)
 */
export default {
  title: "Edit skill",
  notFound: "Skill {{id}} is not in the current snapshot; it may have been removed or not synced yet.",
  subtitle: "{{id}} · contributed by {{name}}",
  kernelHint: "Writes go through the kernel (agx_skill_contribute): updates by skill_id, idempotent, cache sync included",
  fName: "Skill name",
  fNameHint: "The kernel uses the name as an idempotency key — renaming creates a new identity",
  fCategory: "Category",
  fCategoryHint: "e.g. browser / github / search / mcp / other",
  fDesc: "One-line description",
  fDescHint: "This is what people scan in the list to decide whether to open it",
  fTags: "Tags",
  fTagsHint: "Comma separated",
  contentLabel: "Body (Markdown)",
  contentHint: "Rule of thumb: if someone can follow it → it belongs here; if it only says who did what → that is a notice, not a skill.",
  updated: "Skill updated",
  errNameEmpty: "Skill name cannot be empty (the kernel uses the name as an idempotency key; an empty name creates junk data)",
  errContentEmpty: "Skill body cannot be empty",
  footerHint: "Use count / helpful votes are not edited here (those are usage signals, not descriptions)",
} as const;
