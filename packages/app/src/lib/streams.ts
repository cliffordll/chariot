/**
 * Anthropic Messages SSE 文本增量 + usage 抽取(浏览器侧)。
 *
 * 对齐 `chariot/sdk/streams.py::ChatStream`(Python 端)。0.2.0 起 chariot 单
 * 协议化,只处理 Messages SSE:
 * - 文本增量:`content_block_delta` + `delta.type == "text_delta"` → `delta.text`
 * - Usage 累加:`message_start.message.usage.input_tokens` 起始;
 *   `message_delta.usage.output_tokens` 累计
 *
 * 其余事件(tool_use / thinking / error)0.2.x 忽略。
 */

import { iterSse, type SseFrame } from "@/lib/sse";

/** 消费一条 Messages SSE 流:边 yield 文本增量,边累加 usage。 */
export class ChatStream {
  inputTokens = 0;
  outputTokens = 0;

  async *textDeltas(resp: Response, signal?: AbortSignal): AsyncGenerator<string> {
    for await (const frame of iterSse(resp, signal)) {
      this.updateUsage(frame);
      const t = extractText(frame);
      if (t) yield t;
    }
  }

  private updateUsage(frame: SseFrame): void {
    const { event, data } = frame;
    const etype = event ?? (typeof data.type === "string" ? data.type : null);

    if (etype === "message_start") {
      const msg = data.message;
      if (isObj(msg)) {
        const u = msg.usage;
        if (isObj(u)) {
          this.inputTokens = toInt(u.input_tokens);
          this.outputTokens = toInt(u.output_tokens);
        }
      }
    } else if (etype === "message_delta") {
      const u = data.usage;
      if (isObj(u)) {
        const ot = u.output_tokens;
        // Anthropic message_delta.usage.output_tokens 是累计值
        if (typeof ot === "number") this.outputTokens = ot;
      }
    }
  }
}

function extractText(frame: SseFrame): string {
  const { event, data } = frame;
  const etype = event ?? (typeof data.type === "string" ? data.type : null);
  if (etype !== "content_block_delta") return "";
  const delta = data.delta;
  if (!isObj(delta) || delta.type !== "text_delta") return "";
  return typeof delta.text === "string" ? delta.text : "";
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function toInt(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.trunc(v) : 0;
}
