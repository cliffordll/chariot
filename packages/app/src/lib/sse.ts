/**
 * 浏览器侧 SSE 解析:`fetch` 的 `ReadableStream` → `(event, data)` 异步迭代器。
 *
 * 与 `chariot/sdk/streams.py::_iter_sse` 对称,差异只在语言:
 * - 按 `\n\n` / `\r\n\r\n` 切帧
 * - 多个 `data:` 行用 `\n` 拼接后 JSON 解析
 * - `data: [DONE]` sentinel 跳过
 * - 空帧 / 以 `:` 开头的注释帧跳过
 *
 * 只负责解帧;format → 文本增量 / usage 的抽取放 `streams.ts`。
 */

export interface SseFrame {
  event: string | null;
  data: Record<string, unknown>;
}

/** 把 fetch 响应的 body 解析成 SSE 帧流。调用方保证 `resp.body` 非空。 */
export async function* iterSse(resp: Response, signal?: AbortSignal): AsyncGenerator<SseFrame> {
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort);

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      while (true) {
        const sep = findSeparator(buffer);
        if (!sep) break;
        const frameStr = buffer.slice(0, sep.start);
        buffer = buffer.slice(sep.end);
        const frame = parseFrame(frameStr);
        if (frame) yield frame;
      }
    }
    // 尾部容忍无结束空行
    const tail = buffer.trim();
    if (tail) {
      const frame = parseFrame(buffer);
      if (frame) yield frame;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    try {
      reader.releaseLock();
    } catch {
      // already released by cancel()
    }
  }
}

function findSeparator(buf: string): { start: number; end: number } | null {
  const crlf = buf.indexOf("\r\n\r\n");
  const lf = buf.indexOf("\n\n");
  if (crlf !== -1 && (lf === -1 || crlf < lf)) {
    return { start: crlf, end: crlf + 4 };
  }
  if (lf !== -1) {
    return { start: lf, end: lf + 2 };
  }
  return null;
}

function parseFrame(frame: string): SseFrame | null {
  let event: string | null = null;
  const dataLines: string[] = [];
  for (const raw of frame.split("\n")) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join("\n");
  if (dataStr.trim() === "[DONE]") return null;
  try {
    const parsed: unknown = JSON.parse(dataStr);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return { event, data: parsed as Record<string, unknown> };
  } catch {
    return null;
  }
}
