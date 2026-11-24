/**
 * Project edit dialog (row action in the project list)
 */
export default {
  title: "Edit project",
  kernelHint: "Writes go through the kernel (agx_update_project): field tiering and ownership checks happen there",
  fName: "Project name",
  fAbbr: "Abbreviation",
  fAbbrHint: "Lowercase letters only; used as the agent naming prefix",
  fLogo: "Logo",
  fLogoHint: "A URL or a local path under /brand/; leave empty to fall back to the initial letter",
  fSourceDir: "Source directory",
  fXdesc: "Project brief",
  fXdescHint: "Markdown; this is what agents read at startup (agx_project.x_desc)",
  fActor: "Acting as",
  fActorHint: "The kernel decides permissions from this (needs L5: project_manager / workshop_manager)",
  actorPlaceholder: "Pick the acting identity",
  errNameEmpty: "Project name cannot be empty",
  errNoChanges: "Nothing has changed",
  errActorMissing: "Pick the acting identity (requires project_manager / workshop_manager)",
  updated: "Project updated",
  cancel: "Cancel",
  save: "Save",
} as const;
