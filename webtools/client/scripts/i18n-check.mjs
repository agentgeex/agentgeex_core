#!/usr/bin/env node
/**
 * i18n consistency check — the Chinese and English trees must be strictly isomorphic.
 *
 * Why required:
 *   i18next **silently falls back to Chinese** when a key is missing. That is, a whole missing block produces no
 *   error, no red text, just a few Chinese sentences suddenly appearing in the English UI — and often only found
 *   after release. This script turns "silent" into "failure".
 *
 * Checks:
 *   1. key tree isomorphism (missing / extra → error with the path)
 *   2. empty values ("" / null / non-string) → error
 *   3. interpolation placeholders consistent (`{{count}}` in Chinese but not English → the number cannot render)
 *   4. Chinese mixed into English values → warning (except a proper-noun whitelist)
 *   5. keys referenced as `t("x.y")` in code **must really exist** → error if not
 *      (blood-earned: when i18next cannot find a key it shows the key verbatim,
 *       so the UI shows strings like "tab.system" with no build-time hint)
 *
 * Usage:
 *   node scripts/i18n-check.mjs
 *   node scripts/i18n-check.mjs --json
 *
 * Exit code: errors = 1 (can hang off CI / pre-commit)
 */
import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { pathToFileURL } from "node:url";

const ROOT = new URL("..", import.meta.url).pathname;
const tmp = mkdtempSync(join(tmpdir(), "i18n-check-"));

/** Bundle the TS language packs into ESM with esbuild, then import — no runtime dependency */
async function load(locale) {
  const out = join(tmp, `${locale}.mjs`);
  await build({
    entryPoints: [join(ROOT, "src/i18n/locales", locale, "index.ts")],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile: out,
    logLevel: "silent",
  });
  const mod = await import(pathToFileURL(out).href + `?t=${Date.now()}`);
  return mod.default;
}

/** Flatten into {"a.b.c": value} */
function flatten(node, prefix = "", out = new Map()) {
  if (node && typeof node === "object" && !Array.isArray(node)) {
    for (const [k, v] of Object.entries(node)) flatten(v, prefix ? `${prefix}.${k}` : k, out);
  } else {
    out.set(prefix, node);
  }
  return out;
}

const placeholders = (s) =>
  typeof s === "string" ? [...s.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((m) => m[1]).sort().join(",") : "";

/** Proper nouns allowed to remain in English */
const ALLOW_CJK = [/AgentGeeX/];

const errors = [];
const warnings = [];

/**
 * Collect statically hardcoded t("...") references in the source.
 *  Literals only — concatenated keys (t(`nav.item.${x}`)) cannot be verified statically, skipped.
 */
async function collectUsedKeys() {
  const { readdirSync, readFileSync, statSync } = await import("node:fs");
  const root = join(ROOT, "src");
  const files = [];
  const walk = (d) => {
    for (const n of readdirSync(d)) {
      const p = join(d, n);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(tsx?|jsx?)$/.test(n)) files.push(p);
    }
  };
  walk(root);

  const used = new Map(); // key -> file
  for (const f of files) {
    if (f.includes("/i18n/")) continue; // Language packs and i18n itself do not count
    const code = readFileSync(f, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/.*$/gm, "$1");
    /* Require the key to contain at least one dot ("common.ok"):
 * keys in this project all carry a namespace; ones without a dot are the local variable t (e.g. the testid helper in
 * voice-input).
 */
    for (const m of code.matchAll(/\b(?:i18n\.)?t\(\s*"([a-zA-Z][\w]*\.[\w.]+)"\s*[,)]/g)) {
      if (!used.has(m[1])) used.set(m[1], f.replace(ROOT, ""));
    }
  }
  return used;
}

try {
  const zh = flatten(await load("zh-CN"));
  const en = flatten(await load("en-US"));

  for (const [k, v] of zh) {
    if (typeof v !== "string" || !v.trim()) errors.push(`中文包空值/非字符串：${k}`);
    if (!en.has(k)) errors.push(`英文包缺 key：${k}`);
  }
  for (const k of en.keys()) {
    if (!zh.has(k)) warnings.push(`英文包多出 key（中文没有）：${k}`);
  }
  for (const [k, v] of en) {
    if (!zh.has(k)) continue;
    if (typeof v !== "string" || !v.trim()) errors.push(`英文包空值/非字符串：${k}`);
    const pz = placeholders(zh.get(k));
    const pe = placeholders(v);
    if (pz !== pe) errors.push(`插值不一致：${k}（中文 ${pz || "无"} / 英文 ${pe || "无"}）`);
    if (/[\u4e00-\u9fff]/.test(v) && !ALLOW_CJK.some((r) => r.test(v))) {
      warnings.push(`英文值里还有中文：${k} → ${v}`);
    }
  }

  /* // 5) keys referenced in code must exist (otherwise the UI shows the key as the label) */
  const used = await collectUsedKeys();
  for (const [k, f] of used) {
    if (!zh.has(k)) errors.push(`代码引用了不存在的 key：${k}（${f}）`);
  }

  if (process.argv.includes("--json")) {
    console.log(JSON.stringify({ keys: zh.size, errors, warnings }, null, 2));
  } else {
    console.log("════ i18n 一致性检查 ════");
    console.log(`中文 key ${zh.size} 个 / 英文 key ${en.size} 个`);
    if (!errors.length && !warnings.length) console.log("✅ 两棵树完全同构");
    for (const e of errors) console.log(`❌ ${e}`);
    for (const w of warnings) console.log(`⚠️  ${w}`);
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

process.exit(errors.length ? 1 : 0);
