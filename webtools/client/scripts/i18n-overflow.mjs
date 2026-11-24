#!/usr/bin/env node
/**
 * Overflow detection -- the **only objective criterion** for whether the i18n work can be closed.
 *
 * ## Why it is required
 * Four Chinese characters may double in width in English (a 48px phrase becomes "Quick commands" at 130px).
 * Reviewing page by page by hand always leaves gaps, and the gaps are usually the tightest button rows /
 * table headers / navigation.
 *
 * ## How it judges
 * For each element check `scrollWidth > clientWidth + 2`:
 *   · text clipped by the container (elements with truncate/ellipsis are not reported -- that is
 *     **intentional** and has a title)
 *   · the container itself cannot scroll (overflow-auto/scroll are not reported -- that is by design)
 *   · only report **visible elements that carry text** (an empty box means nothing)
 *
 * ## Which language to run
 * `en-XA` by default (pseudo-localization, length ~2.2x Chinese, longer than real English).
 * **If pseudo text does not blow up, real English definitely will not.** You can also use `--lang=zh-CN` to
 * find existing squeeze problems in the current Chinese version.
 *
 * Usage:
 *   node scripts/i18n-overflow.mjs                 # en-XA, scan all business pages
 *   node scripts/i18n-overflow.mjs --lang=zh-CN    # check squeeze in the current Chinese version
 *   node scripts/i18n-overflow.mjs --strict        # exit 1 on any overflow (for CI)
 *   node scripts/i18n-overflow.mjs --route=/agents # scan only one route
 */
import { chromium } from "playwright";

const BASE = process.env.BASE || "http://127.0.0.1:5190";
const args = process.argv.slice(2);
const lang = (args.find((a) => a.startsWith("--lang=")) || "--lang=en-XA").split("=")[1];
const strict = args.includes("--strict");
const only = (args.find((a) => a.startsWith("--route=")) || "").split("=")[1];

const ROUTES = only
  ? [only]
  : [
      "/system", "/quick-access", "/agents", "/sessions", "/tasks", "/executions",
      "/quick-cmds", "/skills", "/hosts", "/notices", "/projects", "/project-groups", "/plans",
    ];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

/* Set the language before entering the page (i18n reads localStorage at module init) */
await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.getByTestId("login-username").fill("admin");
await page.getByTestId("login-password").fill(["local", "dev"].join("-"));
await page.getByTestId("login-submit").click();
await page.waitForTimeout(2500);
/* ★ The language is read once from localStorage when the i18n module initializes -- the value must be set and
   then the **whole page reloaded**, otherwise you change "the copy already initialized" and the UI does not
   follow (hit this in practice: lang was still zh-CN). */
await page.evaluate((l) => localStorage.setItem("agentgeex.locale", l), lang);
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const applied = await page.evaluate(() => document.documentElement.lang);
if (applied !== lang) {
  console.error(`❌ 语言没切过去（想要 ${lang}，实际 ${applied}）——检测结果不可信，先修这个`);
  await browser.close();
  process.exit(2);
}

/** Find overflow elements in the page (returns a readable selector + overflow pixels + text) */
const SCAN = () => {
  const out = [];
  const sel = (el) => {
    const parts = [];
    let n = el;
    for (let i = 0; i < 4 && n && n.nodeType === 1; i++) {
      let p = n.tagName.toLowerCase();
      if (n.dataset && n.dataset.testid) p += `[data-testid="${n.dataset.testid}"]`;
      else if (n.className && typeof n.className === "string") {
        const cls = n.className.split(/\s+/).filter(Boolean).slice(0, 2).join(".");
        if (cls) p += "." + cls;
      }
      parts.unshift(p);
      n = n.parentElement;
    }
    return parts.join(" > ");
  };
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 6) continue;
    const text = (el.textContent || "").trim();
    if (!text || text.length < 2) continue;
    /* Things designed to scroll / be clipped are not reported */
    if (/(auto|scroll)/.test(cs.overflowX)) continue;
    if (/(ellipsis|clip)/.test(cs.textOverflow) && cs.whiteSpace === "nowrap") continue;
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") continue;
    /* Only report "text really cut off": all children are inside the box, but the whole overflows */
    const delta = el.scrollWidth - el.clientWidth;
    if (delta > 2) {
      out.push({ sel: sel(el), delta, text: text.replace(/\s+/g, " ").slice(0, 60) });
    }
  }
  /* The same element can be reported by both parent and child (scrollWidth bubbles); de-duplicate by text keeping the outermost */
  const seen = new Set();
  return out.filter((x) => {
    const k = x.text + "|" + x.delta;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
};

let total = 0;
const report = [];
for (const route of ROUTES) {
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1800);
  const hits = await page.evaluate(SCAN);
  total += hits.length;
  report.push({ route, hits });
}

console.log(`════ 溢出检测（lang=${lang}）════`);
for (const { route, hits } of report) {
  console.log(`\n${route}  ${hits.length === 0 ? "✅" : `❌ ${hits.length} 处`}`);
  for (const h of hits.slice(0, 8)) {
    console.log(`   +${h.delta}px  ${h.text}`);
    console.log(`           ${h.sel}`);
  }
  if (hits.length > 8) console.log(`   … 另有 ${hits.length - 8} 处（--all 可看全）`);
}
console.log(`\n合计 ${total} 处溢出`);

await browser.close();
if (strict && total > 0) process.exit(1);
