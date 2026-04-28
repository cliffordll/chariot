/**
 * Anthropic Messages SSE → typed events + usage 累加(浏览器侧)。
 *
 * 对齐 `chariot/sdk/streams.py::ChatStream`(Python 端,0.5.0)。0.5.0 起 server
 * 在一条响应里串多个 `message_start ... message_stop` 块(slow path 工具循环),
 * 中间还会插入 server 合成的 role=user tool_result message。本模块吐 typed
 * `StreamEvent` 给上层渲染层。
 *
 * - `text`:`content_block_delta` 的 `text_delta`,逐 token 触发
 * - `tool_use`:tool_use block 在 `content_block_stop` 时一次性吐(累积完
 *   `input_json_delta` 并 JSON 解析后才能拿完整 input)
 * - `tool_result`:server 合成的 user role tool_result block 在 `content_block_stop`
 *   时吐
 * - `turn_complete`:`message_stop` 触发,带 role + 本轮 usage
 * - `stream_done`:整个 HTTP 响应结束,带跨 turn 累计 usage
 *
 * Usage 累加规则:仅 `role=assistant` turn 累加;synthetic user turn 的 usage 都是 0。
 */

import { iterSse, type SseFrame } from "@/lib/sse";

export interface TextEvent {
  kind: "text";
  text: string;
}

export interface ToolUseEvent {
  kind: "tool_use";
  toolUseId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
}

export interface ToolResultEvent {
  kind: "tool_result";
  toolUseId: string;
  /** 把 tool_result.content 的所有 text block 拼成的字符串(实际场景文本占绝大多数)。 */
  toolResultContent: string;
  isError: boolean;
}

export interface TurnCompleteEvent {
  kind: "turn_complete";
  /** "assistant"(LLM 那轮) / "user"(server 合成的 tool_result message)。 */
  role: string;
  inputTokens: number;
  outputTokens: number;
}

export interface StreamDoneEvent {
  kind: "stream_done";
  inputTokens: number;
  outputTokens: number;
}

export type StreamEvent =
  | TextEvent
  | ToolUseEvent
  | ToolResultEvent
  | TurnCompleteEvent
  | StreamDoneEvent;

/** 消费一条 Messages SSE 流:吐 typed events,边累加 usage。 */
export class ChatStream {
  inputTokens = 0;
  outputTokens = 0;

  /** 主出口:async generator 吐 StreamEvent。 */
  async *events(resp: Response, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
    let currentRole = "assistant";
    let turnInputTokens = 0;
    let turnOutputTokens = 0;
    // 单 content block 累积(同一时间只在一个 block 里)
    let inToolUse = false;
    let tuId = "";
    let tuName = "";
    let tuInputBuf = "";
    let inToolResult = false;
    let trId = "";
    let trContentBuf = "";
    let trIsError = false;

    for await (const frame of iterSse(resp, signal)) {
      const etype = frameType(frame);
      if (!etype) continue;

      if (etype === "message_start") {
        const msg = frame.data.message;
        if (isObj(msg)) {
          const role = msg.role;
          currentRole = typeof role === "string" ? role : "assistant";
          const u = msg.usage;
          if (isObj(u)) {
            turnInputTokens = toInt(u.input_tokens);
            turnOutputTokens = toInt(u.output_tokens);
          } else {
            turnInputTokens = 0;
            turnOutputTokens = 0;
          }
        }
        continue;
      }

      if (etype === "content_block_start") {
        const block = frame.data.content_block;
        if (isObj(block)) {
          const btype = block.type;
          if (btype === "tool_use") {
            inToolUse = true;
            tuId = typeof block.id === "string" ? block.id : "";
            tuName = typeof block.name === "string" ? block.name : "";
            tuInputBuf = "";
          } else if (btype === "tool_result") {
            inToolResult = true;
            trId = typeof block.tool_use_id === "string" ? block.tool_use_id : "";
            trContentBuf = extractToolResultText(block.content);
            trIsError = block.is_error === true;
          }
        }
        continue;
      }

      if (etype === "content_block_delta") {
        const delta = frame.data.delta;
        if (!isObj(delta)) continue;
        const dtype = delta.type;
        if (dtype === "text_delta") {
          const text = delta.text;
          if (typeof text === "string" && text) {
            yield { kind: "text", text };
          }
        } else if (dtype === "input_json_delta" && inToolUse) {
          const pj = delta.partial_json;
          if (typeof pj === "string") tuInputBuf += pj;
        }
        continue;
      }

      if (etype === "content_block_stop") {
        if (inToolUse) {
          let parsedInput: Record<string, unknown> = {};
          try {
            const parsed: unknown = JSON.parse(tuInputBuf || "{}");
            if (isObj(parsed)) parsedInput = parsed;
          } catch {
            parsedInput = {};
          }
          yield {
            kind: "tool_use",
            toolUseId: tuId,
            toolName: tuName,
            toolInput: parsedInput,
          };
          inToolUse = false;
          tuId = "";
          tuName = "";
          tuInputBuf = "";
        } else if (inToolResult) {
          yield {
            kind: "tool_result",
            toolUseId: trId,
            toolResultContent: trContentBuf,
            isError: trIsError,
          };
          inToolResult = false;
          trId = "";
          trContentBuf = "";
          trIsError = false;
        }
        // text block stop 不吐 event(text_delta 已逐增量吐了)
        continue;
      }

      if (etype === "message_delta") {
        const u = frame.data.usage;
        if (isObj(u)) {
          const ot = u.output_tokens;
          // Anthropic message_delta.usage.output_tokens 是累计值
          if (typeof ot === "number") turnOutputTokens = ot;
        }
        continue;
      }

      if (etype === "message_stop") {
        yield {
          kind: "turn_complete",
          role: currentRole,
          inputTokens: turnInputTokens,
          outputTokens: turnOutputTokens,
        };
        // 仅 assistant turn 累加(synthetic user 的 tool_result usage 都是 0)
        if (currentRole === "assistant") {
          this.inputTokens += turnInputTokens;
          this.outputTokens += turnOutputTokens;
        }
        turnInputTokens = 0;
        turnOutputTokens = 0;
        continue;
      }

      // 其它事件(error / 自定义)忽略
    }

    yield {
      kind: "stream_done",
      inputTokens: this.inputTokens,
      outputTokens: this.outputTokens,
    };
  }

  /** 兼容入口:仅吐 text 增量(等价 events() 过滤 kind=='text')。 */
  async *textDeltas(resp: Response, signal?: AbortSignal): AsyncGenerator<string> {
    for await (const ev of this.events(resp, signal)) {
      if (ev.kind === "text" && ev.text) yield ev.text;
    }
  }
}

function frameType(frame: SseFrame): string | null {
  const { event, data } = frame;
  if (event) return event;
  return typeof data.type === "string" ? data.type : null;
}

function extractToolResultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const texts: string[] = [];
    for (const b of content) {
      if (!isObj(b)) continue;
      if (b.type === "text" && typeof b.text === "string") {
        texts.push(b.text);
      }
    }
    return texts.join("");
  }
  return "";
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function toInt(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.trunc(v) : 0;
}
