#!/usr/bin/env node
/**
 * scripts/gen-brand-logos.mjs
 * Scans public/brand/{projects,tasks} and writes the two lists in src/lib/brand-logos.ts.
 *
 * Usage: node scripts/gen-brand-logos.mjs
 * When: run once after image assets (produced by agx_image) land in public/brand/;
 *       anything not generated is missing from the list → components fall back to initials, no 404s.
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const OUT = path.join(ROOT, "src/lib/brand-logos.ts");
const EXTS = new Set([".png", ".jpg", ".jpeg", ".webp", ".svg"]);

function scan(sub) {
  const dir = path.join(ROOT, "public/brand", sub);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => EXTS.has(path.extname(f).toLowerCase()))
    .sort()
    .map((f) => [path.basename(f, path.extname(f)), `/brand/${sub}/${f}`]);
}

const projects = scan("projects");
const tasks = scan("tasks");

const fmt = (rows) =>
  rows.length
    ? rows.map(([k, v]) => `  ${JSON.stringify(k)}: ${JSON.stringify(v)},`).join("\n")
    : "  // （空：图片尚未生成，全部走首字母兜底）";

const content = `/**
 * lib/brand-logos.ts — 项目 / 任务 logo 解析（本地静态图）
 *
 * 目录与命名（与 agx_image 对齐，产品负责人 2026-10-20 定）：
 *   - 项目：\`public/brand/projects/{project_id}.png\`
 *   - 任务：\`public/brand/tasks/{task_labs}.png\`
 *     ★ task_labs 会重复（general / watchdog_alert / universal 各 70+ 行），
 *       所以 **一张图按 task_labs 去重共用**，同一 labs 的所有 task 行显示同一 logo。
 *
 * 为什么是「显式清单」而不是「直接拼路径」：
 *   拼路径 = 每张没生成的图都会 404（控制台一片红、图片闪烁）。
 *   清单由 \`scripts/gen-brand-logos.mjs\` 扫描目录自动生成 —— 图片到位后跑一次脚本即可，
 *   其余对象继续走首字母兜底，全程 0 404。
 *
 * 解析优先级（组件 entity-logo.tsx 实现）：
 *   1. 后端字段（ao_project.project_logo / ao_task_base.task_logo）
 *   2. 本地品牌图（本文件清单）
 *   3. 首字母 + 确定性配色
 *
 * ★ 本文件由 scripts/gen-brand-logos.mjs 生成，勿手改（改了就下次生成被覆盖）。
 */

/** 项目 logo 清单：project_id → 文件 */
export const BRAND_PROJECT_LOGOS: Record<string, string> = {
${fmt(projects)}
};

/** 任务 logo 清单：task_labs → 文件（按 labs 去重） */
export const BRAND_TASK_LOGOS: Record<string, string> = {
${fmt(tasks)}
};

/** 项目 logo：命中清单返回路径，否则空串（调用方回退首字母）。 */
export function resolveBrandProjectLogo(id?: string): string {
  if (!id) return "";
  return BRAND_PROJECT_LOGOS[id] || "";
}

/** 任务 logo：按 task_labs 解析（传 task_id 请先由调用方换算成 labs）。 */
export function resolveBrandTaskLogo(labs?: string): string {
  if (!labs) return "";
  return BRAND_TASK_LOGOS[labs] || "";
}
`;

fs.writeFileSync(OUT, content, "utf8");
console.log(`✅ 生成 ${path.relative(ROOT, OUT)} — projects ${projects.length} / tasks ${tasks.length}`);
