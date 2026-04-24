import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

const POLL_INTERVAL_MS = 10_000;

/**
 * 全局 server 心跳:每 10s ping 一次 `/admin/ping`,失败时顶部横条提示 + 手动 Retry。
 * 用户可见的行为:
 * - 断连:红色横条滑出(顶部)
 * - 恢复:自动隐藏(不打扰)
 * - Retry:立刻再试一次 ping,不等轮询
 */
export function ServerStatusBanner() {
  const [down, setDown] = useState(false);
  const [checking, setChecking] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      const { ok } = await api.ping();
      setDown(!ok);
    } catch {
      setDown(true);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    // 立即 ping 一次;之后走轮询
    void check();
    const id = setInterval(() => void check(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [check]);

  if (!down) return null;

  return (
    <div className="sticky top-0 z-50 border-b border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
      <div className="mx-auto flex max-w-screen-lg items-center justify-between gap-3">
        <span>
          <strong>Chariot server 失联。</strong> 前端暂时无法拉取数据;确认 server 仍在运行,或
          <code className="mx-1 rounded bg-muted px-1 text-xs">chariot start</code>
          重新拉起。
        </span>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void check()}
          disabled={checking}
        >
          {checking ? "Retrying…" : "Retry"}
        </Button>
      </div>
    </div>
  );
}
