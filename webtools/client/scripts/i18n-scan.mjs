#!/usr/bin/env node
/**
 * i18n inventory — count "how much Chinese is still outside the language packs", progress by file/directory.
 *
 * Why this script (instead of grep):
 *   1) hardcoded Chinese is spread across 165 files; by eye you never know if it is done;
 *   2) i18n is done in batches, **each batch must lower the number** — without numbers you go by feel;
 *   3) it also blocks "regressions to Chinese": CI compares against a baseline and errors when the number grows.
 *
 * Usage:
 *   node scripts/i18n-scan.mjs            # summary + the 20 dirtiest files
 *   node scripts/i18n-scan.mjs --all      # all files (descending by count)
 *   node scripts/i18n-scan.mjs --json     # machine readable (for CI / baseline comparison)
 *   node scripts/i18n-scan.mjs --baseline # write/read scripts/i18n-baseline.json for comparison
 *
 * Definition (deliberately conservative, prefer over-reporting to missing):
 *   · only count Chinese in **string literals** and **JSX text**, comments do not count (comments are not
 *     user-facing);
 *   · do not count the i18n language packs themselves (those should be Chinese);
 *   · do not count the mock directory (demo data is not UI copy, but reported on a separate line as a reminder).
 */

import { readdirSync, readFileSync, statSync, writeFileSync, existsSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "src");
const BASELINE = join(dirname(fileURLToPath(import.meta.url)), "i18n-baseline.json");

const HAN = /[\u4e00-\u9fff]/;
/** Paths allowed to keep Chinese (language packs / demo data / generated artifacts) */
const SKIP = [
  "src/i18n/locales/",   // Language packs: these should be Chinese
  "src/i18n/glossary.ts", // Glossary: the Chinese-English mapping is its content, not copy to translate
  "src/mock/",           // Demo data
];

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (/\.(tsx?|jsx?)$/.test(name)) out.push(p);
  }
  return out;
}

/** Strip comments before counting, so design notes are not counted as copy to translate */
function stripComments(code) {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/.*$/gm, (m, p1) => p1 + " ".repeat(Math.max(0, m.length - p1.length)));
}

/** How many "user-facing" Chinese occurrences are in one line */
function countLine(line) {
  let n = 0;
  const hits = [];

  // 1) JSX text nodes: >content<
  for (const m of line.matchAll(/>([^<>{}\n]+)</g)) {
    const t = m[1].trim();
    if (t && HAN.test(t)) {
      n++;
      hits.push(t.slice(0, 30));
    }
  }
  // 2) JSX attributes / string literals: title="…" or "…" / '…' / `…`
  for (const m of line.matchAll(/(["'`])((?:\\.|(?!\1)[^\\])*?)\1/g)) {
    const t = m[2];
    // Filter: Chinese in pure markup (class names, ids, paths) is rare and already excluded by HAN;
    // Here also exclude "looks like a code identifier": no spaces and only Chinese + underscores
    if (HAN.test(t) && !/^[\w-]+$/.test(t)) {
      n++;
      hits.push(t.slice(0, 30));
    }
  }
  return { n, hits };
}

const files = walk(SRC)
  .map((p) => relative(ROOT, p).replace(/\\/g, "/"))
  .filter((rel) => !SKIP.some((s) => rel.startsWith(s)));

const rows = [];
let total = 0;
for (const rel of files) {
  const raw = readFileSync(join(ROOT, rel), "utf8");
  const code = stripComments(raw);
  let n = 0;
  const samples = [];
  code.split("\n").forEach((line) => {
    const r = countLine(line);
    n += r.n;
    if (r.hits.length && samples.length < 3) samples.push(...r.hits.slice(0, 1));
  });
  if (n > 0) {
    rows.push({ file: rel, count: n, samples });
    total += n;
  }
}

rows.sort((a, b) => b.count - a.count);

/** Summarize by directory: see which layer is dirtiest, deciding where the next batch starts */
const byDir = new Map();
for (const r of rows) {
  const dir = r.file.split("/").slice(0, 3).join("/");
  byDir.set(dir, (byDir.get(dir) || 0) + r.count);
}
const dirs = [...byDir.entries()].sort((a, b) => b[1] - a[1]);

const args = process.argv.slice(2);
const asJson = args.includes("--json");
const showAll = args.includes("--all");
const useBaseline = args.includes("--baseline");

if (asJson) {
  console.log(JSON.stringify({ total, files: rows.length, scanned: files.length, rows, dirs }, null, 2));
} else {
  console.log("════ i18n 盘点 ════");
  console.log(`扫描 ${files.length} 个文件，其中 ${rows.length} 个含未国际化中文`);
  console.log(`待国际化文案：${total} 处`);
  console.log("");
  console.log("── 按目录（top 8）──");
  for (const [dir, n] of dirs.slice(0, 8)) {
    const bar = "█".repeat(Math.max(1, Math.round((n / dirs[0][1]) * 30)));
    console.log(`${String(n).padStart(5)}  ${bar} ${dir}`);
  }
  console.log("");
  console.log(`── 最需要处理的文件（${showAll ? "全部" : "top 20"}）──`);
  for (const r of showAll ? rows : rows.slice(0, 20)) {
    console.log(`${String(r.count).padStart(5)}  ${r.file}`);
    if (r.samples.length) console.log(`        例：${r.samples.join(" | ")}`);
  }
}

if (useBaseline) {
  if (existsSync(BASELINE) && !args.includes("--write-baseline")) {
    const base = JSON.parse(readFileSync(BASELINE, "utf8"));
    const diff = total - base.total;
    console.log("");
    console.log(`基线 ${base.total} → 现在 ${total}（${diff > 0 ? "↑ +" : diff < 0 ? "↓ " : "＝ "}${diff}）`);
    if (diff > 0) {
      console.error("❌ 待国际化文案比基线**变多**了：先国际化再提交，或说明为什么新增中文。");
      process.exit(1);
    }
  } else if (args.includes("--write-baseline")) {
    writeFileSync(BASELINE, JSON.stringify({ total, at: new Date().toISOString() }, null, 2));
    console.log(`\n已写基线：${BASELINE} → ${total}`);
  }
}
