/**
 * 三格式 SSE 文本增量 + usage 抽取。
 *
 * 对齐 `chariot/sdk/streams.py`(Python 端的 `ChatStream`):
 * - 文本抽取:
 *   · MESSAGES       → `content_block_delta` + `delta.type == "text_delta"` → `delta.text`
 *   · CHAT_COMPLETIONS → `choices[0].delta.content`
 *   · RESPONSES      → `type == "response.output_text.delta"` → `delta`(字符串)
 * - Usage 抽取:
 *   · MESSAGES       → `message_start.message.usage.input_tokens` + `message_delta.usage.output_tokens`(累计)
 *   · CHAT_COMPLETIONS → 最后一个 chunk 的 `usage.{prompt,completion}_tokens`(需 `stream_options.include_usage`)
 *   · RESPONSES      → `response.completed.response.usage.{input,output}_tokens`
 *
 * 其余事件(tool_use / thinking / error)v0.1 忽略。
 */

import { iterSse, type SseFrame } from "@/lib/sse";
import { Protocol } from "@/lib/api";

/** 消费一条流:边 yield 文本增量,边把 usage 累积到 `usage` 对象。 */
export class ChatStream {
  readonly fmt: Protocol;
  inputTokens = 0;
  outputTokens = 0;

  constructor(fmt: Protocol) {
    this.fmt = fmt;
  }

  async *textDeltas(resp: Response, signal?: AbortSignal): AsyncGenerator<string> {
    for await (const frame of iterSse(resp, signal)) {
      this.updateUsage(frame);
      const t = extractText(this.fmt, frame);
      if (t) yield t;
    }
  }

  private updateUsage(frame: SseFrame): void {
    const { event, data } = frame;

    if (this.fmt === Protocol.MESSAGES) {
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
          if (typeof ot === "number") this.outputTokens = ot;
        }
      }
      return;
    }

    if (this.fmt === Protocol.CHAT_COMPLETIONS) {
      const u = data.usage;
      if (isObj(u)) {
        this.inputTokens = toInt(u.prompt_tokens);
        this.outputTokens = toInt(u.completion_tokens);
      }
      return;
    }

    // RESPONSES
    const etype = event ?? (typeof data.type === "string" ? data.type : null);
    if (etype === "response.completed") {
      const r = data.response;
      if (isObj(r)) {
        const u = r.usage;
        if (isObj(u)) {
          this.inputTokens = toInt(u.input_tokens);
          this.outputTokens = toInt(u.output_tokens);
        }
      }
    }
  }
}

function extractText(fmt: Protocol, frame: SseFrame): string {
  const { event, data } = frame;

  if (fmt === Protocol.MESSAGES) {
    const etype = event ?? (typeof data.type === "string" ? data.type : null);
    if (etype !== "content_block_delta") return "";
    const delta = data.delta;
    if (!isObj(delta) || delta.type !== "text_delta") return "";
    return typeof delta.text === "string" ? delta.text : "";
  }

  if (fmt === Protocol.CHAT_COMPLETIONS) {
    const choices = data.choices;
    if (!Array.isArray(choices) || choices.length === 0) return "";
    const c0 = choices[0];
    if (!isObj(c0)) return "";
    const delta = c0.delta;
    if (!isObj(delta)) return "";
    return typeof delta.content === "string" ? delta.content : "";
  }

  // RESPONSES
  const etype = event ?? (typeof data.type === "string" ? data.type : null);
  if (etype !== "response.output_text.delta") return "";
  return typeof data.delta === "string" ? data.delta : "";
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function toInt(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.trunc(v) : 0;
}
