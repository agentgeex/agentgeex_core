#!/usr/bin/env node
/**
 * scripts/brand_export.mjs — export the brand SVG to PNGs of all sizes
 * ────────────────────────────────────────────────────────────────
 * [2026-11-10 Feng] "This icon is not great now... see whether you can use this vector,
 *   or make a logo some other way. AGX, just three letters, put the three letters in it."
 *
 * Why this script exists:
 *   the **single source of truth for the logo is the SVG** (`public/brand/logo-mark.svg`).
 *   PNG / favicon are its outputs — hand-editing a PNG creates a second source of truth,
 *   and the next person to change the SVG leaves a stale PNG that nobody can spot.
 *   So: change the SVG → run this script once → all sizes are regenerated together.
 *
 * Usage:
 *   cd webtools/client && node scripts/brand_export.mjs
 *
 * ★ Transparent background: `omitBackground: true`, so outside the rounded corners it is truly transparent
 *   (not white). A white-backed image on a dark sidebar shows a white square — the most common failure here.
 */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(process.cwd(), "public/brand");
const SRC = path.join(ROOT, "logo-mark.svg");

/** file name → pixel size (all square, the logo itself is square) */
const TARGETS = [
  ["logo-mark.png", 512],
  ["apple-touch-icon.png", 180],
  ["logo-64.png", 64],
  ["favicon-32.png", 32],
  ["favicon-16.png", 16],
];

const svg = fs.readFileSync(SRC, "utf8");
const uri = `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;

const browser = await chromium.launch();
const ctx = await browser.newContext({ deviceScaleFactor: 1 });
const page = await ctx.newPage();

for (const [name, size] of TARGETS) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<style>html,body{margin:0;padding:0;background:transparent}
     img{display:block;width:${size}px;height:${size}px}</style>
     <img src="${uri}">`,
    { waitUntil: "load" },
  );
  await page.waitForTimeout(120);
  const out = path.join(ROOT, name);
  await page.screenshot({ path: out, omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
  console.log(`✓ ${name}  ${size}×${size}`);
}

await browser.close();

/* ── favicon.ico: multi-size container ──
   Modern browsers are fine with `<link rel="icon" type="image/svg+xml">`,
   but Safari / old versions / various bookmark managers still fetch /favicon.ico. It must exist and contain
   multiple sizes, otherwise a 16px tab icon downsamples the 64px image by force and turns to mush. */
const ICO_SIZES = [16, 32, 48, 64];
console.log("\n需要 favicon.ico 时：node scripts/brand_export.mjs --ico（见 README）");
if (process.argv.includes("--ico")) {
  const pngs = [];
  for (const s of ICO_SIZES) {
    const b = await (async () => {
      const br = await chromium.launch();
      const c2 = await br.newContext({ deviceScaleFactor: 1 });
      const p2 = await c2.newPage();
      await p2.setViewportSize({ width: s, height: s });
      await p2.setContent(
        `<style>html,body{margin:0;background:transparent}img{display:block;width:${s}px;height:${s}px}</style><img src="${uri}">`,
        { waitUntil: "load" },
      );
      await p2.waitForTimeout(120);
      const buf = await p2.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: s, height: s } });
      await br.close();
      return buf;
    })();
    pngs.push([s, b]);
  }
  fs.writeFileSync(path.join(ROOT, "favicon.ico"), buildIco(pngs));
  console.log(`✓ favicon.ico  ${ICO_SIZES.join("/")}`);
}

/** hand-built ICO container: 6-byte header + N×16-byte directory + raw data of each PNG */
function buildIco(entries) {
  const count = entries.length;
  const head = Buffer.alloc(6);
  head.writeUInt16LE(0, 0);      // reserved
  head.writeUInt16LE(1, 2);      // type: 1 = icon
  head.writeUInt16LE(count, 4);  // image count
  const dir = Buffer.alloc(16 * count);
  let offset = 6 + 16 * count;
  entries.forEach(([size, data], i) => {
    const o = i * 16;
    dir.writeUInt8(size >= 256 ? 0 : size, o + 0);      // width
    dir.writeUInt8(size >= 256 ? 0 : size, o + 1);      // height
    dir.writeUInt8(0, o + 2);                            // palette count
    dir.writeUInt8(0, o + 3);                            // reserved
    dir.writeUInt16LE(1, o + 4);                         // color planes
    dir.writeUInt16LE(32, o + 6);                        // bpp
    dir.writeUInt32LE(data.length, o + 8);               // data length
    dir.writeUInt32LE(offset, o + 12);                   // data offset
    offset += data.length;
  });
  return Buffer.concat([head, dir, ...entries.map(([, d]) => d)]);
}
