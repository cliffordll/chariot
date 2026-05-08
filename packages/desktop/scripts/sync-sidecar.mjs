#!/usr/bin/env node
/**
 * 把 `dist/chariot-sidecar.exe`(由 scripts/build.py 产出)同步到 Tauri 期待的
 * `tauri/binaries/chariot-sidecar-<host-triplet>.exe` 位置。
 *
 * 0.6.5 S.9 起 sidecar 走 stdio JSON-RPC,exe 名 `chariot-sidecar`(0.5.0 时
 * 是 `chariot-server`,跑 HTTP)。Tauri 2 的 externalBin 用 rustc 当前 host
 * triplet 作文件名后缀。本脚本通过 `rustc -vV` 读 host,避免硬编码。
 *
 * 被 `scripts/build.py --sync-sidecar` 和 `bun --filter=@chariot/desktop sync-sidecar` 调用。
 */

import { execSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
const SRC_EXE = resolve(REPO_ROOT, "dist", "chariot-sidecar.exe");
const DEST_DIR = resolve(__dirname, "..", "tauri", "binaries");

function hostTriplet() {
  const raw = execSync("rustc -vV", { encoding: "utf8" });
  const match = raw.match(/^host:\s*(.+)$/m);
  if (!match) throw new Error("rustc -vV 未输出 host 行;Rust 装好了吗?");
  return match[1].trim();
}

function main() {
  if (!existsSync(SRC_EXE)) {
    console.error(`[sync-sidecar] 源文件不存在:${SRC_EXE}`);
    console.error("  先跑 `uv run --group build python scripts/build.py --target sidecar`");
    process.exit(1);
  }

  const triplet = hostTriplet();
  const destExe = resolve(DEST_DIR, `chariot-sidecar-${triplet}.exe`);

  mkdirSync(DEST_DIR, { recursive: true });
  copyFileSync(SRC_EXE, destExe);

  console.log(`[sync-sidecar] OK  ${SRC_EXE}`);
  console.log(`          -->  ${destExe}`);
}

main();
