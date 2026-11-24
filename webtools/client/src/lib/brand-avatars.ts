/**
 * lib/brand-avatars.ts — brand avatar resolution (local static images)
 *
 * Image location: `public/brand/avatars/<id>.png`
 * Naming rule (set by Feng 2026-10-19): **file name = object id; assistants carry the `case_` / `asst_` prefix**.
 *
 * Resolution order (see resolveBrandAvatar):
 *   1. the input is the full id (`aifr_pm` / `case_aifr_intel`) → direct hit
 *   2. the input is an assistant slug (`aifr_intel` / `defect_hunter`) → add `case_` / `asst_` prefix, then hit
 *   3. the input is a session id (`agent:aifr_pm:case_aifr_intel`) → take the last segment, then hit
 * No hit returns an empty string (the caller falls back to an initials avatar).
 *
 * ★ An explicit list instead of "guessing by path": avoids 404 flicker, and makes a future CDN prefix easy.
 */

/** known brand avatars: id → file (all under /brand/avatars/)
 *  2026-10-19 18:34 fully wired in 83 images (68 agents + 15 assistants); the list is generated from the
 *  directory, do not edit by hand. */
export const BRAND_AVATARS: Record<string, string> = {
  agent_template: "/brand/avatars/agent_template.png",
  agentgeex_agent: "/brand/avatars/agentgeex_agent.png",
  agentic_marketing_book_beta_reader: "/brand/avatars/agentic_marketing_book_beta_reader.png",
  agentic_marketing_book_planner: "/brand/avatars/agentic_marketing_book_planner.png",
  agentic_marketing_book_reviewer: "/brand/avatars/agentic_marketing_book_reviewer.png",
  agentic_marketing_book_writer: "/brand/avatars/agentic_marketing_book_writer.png",
  agentic_marketing_pm: "/brand/avatars/agentic_marketing_pm.png",
  // ★ 2026-11-11: the 4 built-in platform management agents are prefixed with agx_.
  //   the image files were renamed to agx_*.png on 2026-11-11 as well; old and new keys point to the same image.
  agx_agentfather: "/brand/avatars/agx_agentfather.png",
  agx_core: "/brand/avatars/agx_core.png",
  agx_host: "/brand/avatars/agx_host.png",
  agx_projectmother: "/brand/avatars/agx_projectmother.png",
  // ★ 2026-11-11 Feng: the old product name is fully retired, the old id aliases are deleted (no compatibility kept).
  agx_platform_claudecode: "/brand/avatars/agx_platform_claudecode.png",
  agx_platform_codex: "/brand/avatars/agx_platform_codex.png",
  agx_platform_deepseek: "/brand/avatars/agx_platform_deepseek.png",
  agx_platform_openclaw: "/brand/avatars/agx_platform_openclaw.png",
  agx_ui_lead: "/brand/avatars/agx_ui_lead.png",
  aifr_pm: "/brand/avatars/aifr_pm.png",
  asst_defect_hunter: "/brand/avatars/asst_defect_hunter.png",
  book_beta_reader: "/brand/avatars/book_beta_reader.png",
  book_beta_reader_374207: "/brand/avatars/book_beta_reader_374207.png",
  book_efficient_agent: "/brand/avatars/book_efficient_agent.png",
  book_planner: "/brand/avatars/book_planner.png",
  book_planner_458073: "/brand/avatars/book_planner_458073.png",
  book_reviewer: "/brand/avatars/book_reviewer.png",
  book_reviewer_1aace8: "/brand/avatars/book_reviewer_1aace8.png",
  book_up_chain: "/brand/avatars/book_up_chain.png",
  book_writer: "/brand/avatars/book_writer.png",
  book_writer_0ab73b: "/brand/avatars/book_writer_0ab73b.png",
  case_a320_edu: "/brand/avatars/case_a320_edu.png",
  case_agent_research: "/brand/avatars/case_agent_research.png",
  asst_agx_maint: "/brand/avatars/asst_agx_maint.png",
  case_agx_dev: "/brand/avatars/case_agx_dev.png",
  case_aifr_intel: "/brand/avatars/case_aifr_intel.png",
  case_anim_video_research: "/brand/avatars/case_anim_video_research.png",
  case_docs: "/brand/avatars/case_docs.png",
  case_jev_guardrail: "/brand/avatars/case_jev_guardrail.png",
  case_jev_research: "/brand/avatars/case_jev_research.png",
  case_pm_ops: "/brand/avatars/case_pm_ops.png",
  case_skill_research: "/brand/avatars/case_skill_research.png",
  case_t2i_research: "/brand/avatars/case_t2i_research.png",
  case_ui_design: "/brand/avatars/case_ui_design.png",
  case_zeroclaw_research: "/brand/avatars/case_zeroclaw_research.png",
  coingeek_agent: "/brand/avatars/coingeek_agent.png",
  efficient_agent_book_beta_reader: "/brand/avatars/efficient_agent_book_beta_reader.png",
  efficient_agent_book_planner: "/brand/avatars/efficient_agent_book_planner.png",
  efficient_agent_book_reviewer: "/brand/avatars/efficient_agent_book_reviewer.png",
  efficient_agent_book_writer: "/brand/avatars/efficient_agent_book_writer.png",
  expo_dev: "/brand/avatars/expo_dev.png",
  expo_developer: "/brand/avatars/expo_developer.png",
  fxc_math_paper: "/brand/avatars/fxc_math_paper.png",
  klipper_expert: "/brand/avatars/klipper_expert.png",
  medicine_gpt_doctor: "/brand/avatars/medicine_gpt_doctor.png",
  motozolo_agent: "/brand/avatars/motozolo_agent.png",
  motozolo_app_dev: "/brand/avatars/motozolo_app_dev.png",
  permtest_pm: "/brand/avatars/permtest_pm.png",
  py_architect: "/brand/avatars/py_architect.png",
  robert_staff_officer: "/brand/avatars/robert_staff_officer.png",
  super_pm: "/brand/avatars/super_pm.png",
  super_pm_a1f729: "/brand/avatars/super_pm_a1f729.png",
  up_chain_book_beta_reader: "/brand/avatars/up_chain_book_beta_reader.png",
  up_chain_book_planner: "/brand/avatars/up_chain_book_planner.png",
  up_chain_book_reviewer: "/brand/avatars/up_chain_book_reviewer.png",
  up_chain_book_writer: "/brand/avatars/up_chain_book_writer.png",
  win_resource_backfiller: "/brand/avatars/win_resource_backfiller.png",
  win_sem_lead: "/brand/avatars/win_sem_lead.png",
  win_seo_lead: "/brand/avatars/win_seo_lead.png",
  win_social_lead: "/brand/avatars/win_social_lead.png",
  win_ui_lead: "/brand/avatars/win_ui_lead.png",
  win_wordpress_lead: "/brand/avatars/win_wordpress_lead.png",
  wincublabs_agent: "/brand/avatars/wincublabs_agent.png",
  xplane_toliss_dev: "/brand/avatars/xplane_toliss_dev.png",
};

/** take the last segment of a session id (`agent:aifr_pm:case_aifr_intel` → `case_aifr_intel`) */
function lastSegment(key: string): string {
  const parts = key.split(":");
  return parts[parts.length - 1] || key;
}

/**
 * Resolve a brand avatar URL.
 * @param key agent_id / assistant slug / session id all work
 * @returns `/brand/avatars/xxx.png` on a hit, `""` otherwise
 */
export function resolveBrandAvatar(key?: string): string {
  const raw = (key || "").trim();
  if (!raw) return "";

  const candidates = [raw, lastSegment(raw)];
  // assistant slug → add the case_ / asst_ prefix (assistant session last segments carry both prefixes:
  //   `agent:aifr_pm:case_aifr_intel` / `agent:agentgeex_agent:asst_defect_hunter`）
  for (const c of [...candidates]) {
    if (!c.startsWith("case_")) candidates.push(`case_${c}`);
    if (!c.startsWith("asst_")) candidates.push(`asst_${c}`);
  }
  // ★ 2026-11-11: host_manager became per-host unique naming (agx_host_test_host_mac /
  //   agx_host_srv_a / agx_host_test_host_srv …). They share the same "host management" avatar,
  //   otherwise every new machine adds a list entry — the list should record **roles**, not **instances**.
  for (const c of [...candidates]) {
    const m = /^(agx_host)_.+$/.exec(c);
    if (m) candidates.push(m[1]);
  }

  for (const c of candidates) {
    const hit = BRAND_AVATARS[c];
    if (hit) return hit;
  }
  return "";
}
