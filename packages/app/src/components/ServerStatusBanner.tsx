import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

/**
 * sidecar 健康提示横条(0.6.5 S.10 起)。
 *
 * 0.5.0 时是 polling `/admin/ping` 检测 server;0.6.5 改成监听 Tauri
 * `sidecar_exited` event(Rust JsonRpcClient 在 sidecar 进程终止时 emit)。
 * 正常运行时不轮询,sidecar 死了才显示横条 — 用户重启 app 让 Tauri 重新
 * spawn sidecar(0.7.0+ 加自动重 spawn)。
 */
export function ServerStatusBanner() {
  const [down, setDown] = useState(false);
  const [reason, setReason] = useState<string>("");

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void (async () => {
      unlisten = await listen<string>("sidecar_exited", (e) => {
        setDown(true);
        setReason(typeof e.payload === "string" ? e.payload : "(unknown reason)");
      });
    })();
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  if (!down) return null;

  return (
    <div className="sticky top-0 z-50 border-b border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
      <div className="mx-auto flex max-w-screen-lg items-center justify-between gap-3">
        <span>
          <strong>Chariot sidecar 已退出</strong>({reason})。重启 app 恢复服务。
        </span>
      </div>
    </div>
  );
}
