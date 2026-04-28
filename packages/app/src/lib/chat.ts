/**
 * Chat 页核心:历史消息 → Anthropic Messages 请求体 → 一轮流式调用。
 *
 * 0.2.0 起 chariot 单协议化(只接 `/v1/messages`),client 不再持 `fmt`;
 * 历史只存纯文本 `{role, content}[]`,server 端 `Agent` 决定用哪个 Model 实现。
 */

import { apiBase } from "@/lib/api";
import { ChatStream, type StreamEvent } from "@/lib/streams";

const MESSAGES_PATH = "/v1/messages";

export interface ChatTurnMsg {
  role: "user" | "assistant";
  content: string;
}

export interface ChatTurnOpts {
  /** 仅用于 `body.model` 字段(server 会按 active config 改写;影响 `logs.model` 显示)。 */
  model: string;
  maxTokens: number;
  /**
   * Anthropic 采样参数。两者默认 1.0(等同不调);只在 ≠ 1 时才发到 body,以遵循
   * Anthropic 文档"建议两者只挑一个"的语义,避免显式传 1 干扰。
   */
  temperature?: number;
  topP?: number;
  /** 0.4.0 加。非空时通过 `X-Chariot-Conversation` header 带去,server 据此追加 messages。 */
  conversationId?: string | null;
  signal: AbortSignal;
  /**
   * 0.5.0:typed StreamEvent 回调(text / tool_use / tool_result / turn_complete /
   * stream_done)。caller 用 ev.kind 分派;text 用于逐 token 累积,tool_use /
   * tool_result 用于渐进 append blocks 卡片。
   */
  onEvent: (ev: StreamEvent) => void;
}

export interface ChatTurnResult {
  text: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  aborted: boolean;
}

export class ChatError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    const preview = body.slice(0, 200).replace(/\s+/g, " ");
    super(`HTTP ${status}: ${preview}`);
    this.name = "ChatError";
    this.status = status;
    this.body = body;
  }
}

export async function runTurn(
  messages: ChatTurnMsg[],
  opts: ChatTurnOpts,
): Promise<ChatTurnResult> {
  const body: Record<string, unknown> = {
    model: opts.model,
    max_tokens: opts.maxTokens,
    stream: true,
    messages,
  };
  if (opts.temperature !== undefined && opts.temperature !== 1) {
    body.temperature = opts.temperature;
  }
  if (opts.topP !== undefined && opts.topP !== 1) {
    body.top_p = opts.topP;
  }

  const base = await apiBase();
  const t0 = performance.now();
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (opts.conversationId) {
    headers["x-chariot-conversation"] = opts.conversationId;
  }
  let resp: Response;
  try {
    resp = await fetch(base + MESSAGES_PATH, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: opts.signal,
    });
  } catch (e) {
    if (opts.signal.aborted) {
      return { text: "", inputTokens: 0, outputTokens: 0, latencyMs: 0, aborted: true };
    }
    throw e;
  }

  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new ChatError(resp.status, text);
  }

  const stream = new ChatStream();
  // 跨 turn 跟踪:current_turn_text 累积当前 assistant turn 文本;
  // turn_complete(role=assistant)snapshot 到 lastAssistantText,被下一轮覆盖,
  // 流尾留下的就是最终轮的 final 文本(返回给 caller 作 ChatTurnResult.text)。
  let currentTurnText = "";
  let lastAssistantText = "";
  let aborted = false;
  try {
    for await (const ev of stream.events(resp, opts.signal)) {
      opts.onEvent(ev);
      if (ev.kind === "text") {
        currentTurnText += ev.text;
      } else if (ev.kind === "turn_complete" && ev.role === "assistant") {
        lastAssistantText = currentTurnText;
        currentTurnText = "";
      }
    }
  } catch (e) {
    if (opts.signal.aborted) {
      aborted = true;
    } else {
      throw e;
    }
  }

  return {
    text: lastAssistantText,
    inputTokens: stream.inputTokens,
    outputTokens: stream.outputTokens,
    latencyMs: Math.round(performance.now() - t0),
    aborted,
  };
}
