/**
 * Tauri 自动更新前端入口(Dashboard 的 "Check for updates" 按钮用到)。
 *
 * - 浏览器(vite dev)下 Tauri API 不可用 → `isTauri()` 返 false,按钮应隐藏
 * - Tauri 壳内 invoke 后端 command:`check_for_update` / `install_update`
 * - 后端 command 实现在 `packages/desktop/tauri/src/lib.rs`
 */

import { invoke } from "@tauri-apps/api/core";

export interface UpdateCheckResult {
  available: boolean;
  version: string | null;
  notes: string | null;
}

/** 判断当前运行环境是否是 Tauri 壳(而非 vite dev 纯浏览器)。 */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function checkForUpdate(): Promise<UpdateCheckResult> {
  return await invoke<UpdateCheckResult>("check_for_update");
}

export async function installUpdate(): Promise<void> {
  await invoke("install_update");
}
