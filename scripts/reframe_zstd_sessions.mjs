// reframe_zstd_sessions.mjs - re-frame DSH session logs that were compressed into a single frame into "one header frame + one events frame"
//
// Why this is needed:
//   During the 2026-11-14 rename migration the CLI zstd re-compressed everything in one pass, squeezing the header and events into the same frame.
//   The DSH decoder requires "the first frame must be exactly one header line", otherwise it reports an error.
//     corrupt Zstandard session log: first frame is not exactly one header line
//
// Usage:
//   node reframe_zstd_sessions.mjs --dry-run   # report only, no writes
//   node reframe_zstd_sessions.mjs             # actually repair (in place, back up first)
//
// Criteria: if the decompressed first frame is not exactly one line, the file needs re-framing.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import zlib from "node:zlib";
import { execFileSync } from "node:child_process";

const ROOT = path.join(os.homedir(), ".dsh", "sessions");
const BACKUP = path.join(os.homedir(), ".dsh", "backups", `reframe_${new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15)}`);
const CHECKSUM = { params: { [zlib.constants.ZSTD_c_checksumFlag]: 1 } };
const DRY = process.argv.includes("--dry-run");

/** Decompress the whole file with the zstd CLI (handles concatenated frames correctly). */
function decompressAll(file) {
  return execFileSync("zstd", ["-d", "-c", file], { maxBuffer: 1024 * 1024 * 1024 });
}

/** Read the plaintext of the first frame (used to decide whether the header is a single line). */
function firstFramePlain(buf) {
  // zstd frame magic: 0x28 0xB5 0x2F 0xFD (little-endian), skippable frame: 0x184D2A5?
  // Conservative approach here: the zstd CLI cannot decompress only the first frame, so decompress everything and take the first line
  return decompressAll(buf.toString ? buf.toString() : buf);
}

const stats = { total: 0, ok: 0, fixed: 0, failed: 0, skipped: 0 };

function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.isFile() && /^session.*\.jsonl\.zstd$/.test(e.name)) processFile(p);
  }
}

function processFile(file) {
  stats.total++;
  let plain;
  try {
    plain = decompressAll(file);
  } catch (err) {
    console.log(`  [跳过] 解压失败 ${file}: ${err.message}`);
    stats.failed++;
    return;
  }
  const nl = plain.indexOf(10); // '\n'
  if (nl < 0) {
    console.log(`  [跳过] 无换行 ${file}`);
    stats.skipped++;
    return;
  }
  const header = plain.subarray(0, nl + 1);
  const body = plain.subarray(nl + 1);

  // Check: is the existing file's first frame already just the header line?
  // Method: compress the header alone into one frame, then check whether the original file starts with it (length + content compare)
  const headerFrame = zlib.zstdCompressSync(Buffer.from(header), CHECKSUM);
  const raw = fs.readFileSync(file);
  if (raw.length >= headerFrame.length && raw.subarray(0, headerFrame.length).equals(headerFrame)) {
    stats.ok++;
    return; // Already in the correct format
  }

  // Needs re-framing
  if (body.length === 0) {
    stats.skipped++;
    return;
  }
  if (DRY) {
    console.log(`  [待修] ${path.relative(ROOT, file)}  明文 ${plain.length}B  事件体 ${body.length}B`);
    stats.fixed++;
    return;
  }
  try {
    const bodyFrame = zlib.zstdCompressSync(Buffer.from(body), CHECKSUM);
    // Back up the original file
    const bak = path.join(BACKUP, path.relative(ROOT, file));
    fs.mkdirSync(path.dirname(bak), { recursive: true });
    fs.copyFileSync(file, bak);
    // Rewrite in place
    fs.writeFileSync(file, Buffer.concat([headerFrame, bodyFrame]));
    stats.fixed++;
  } catch (err) {
    console.log(`  [失败] ${file}: ${err.message}`);
    stats.failed++;
  }
}

console.log(`扫描 ${ROOT}${DRY ? "  （dry-run，不改动）" : ""}`);
walk(ROOT);
console.log(`\n结果: 共 ${stats.total} | 已正确 ${stats.ok} | 修复 ${stats.fixed} | 跳过 ${stats.skipped} | 失败 ${stats.failed}`);
if (!DRY && stats.fixed > 0) console.log(`原文件已备份到: ${BACKUP}`);
