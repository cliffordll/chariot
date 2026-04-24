/**
 * Chat 页的核心:历史消息 → 请求体构造 + 一轮流式请求。
 *
 * v0 架构:全部打本地 chariot-server,server 里的 Agent 直接生成响应。
 * 没有上游 / api-key 概念。历史只存纯文本 `{role, content}[]`。
 */

import { apiBase, Protocol } from "@/lib/api";
import { ChatStream } from "@/lib/streams";

export interface ChatTurnMsg {
  role: "user" | "assistant";
  content: string;
}

export interface ChatTurnOpts {
  fmt: Protocol;
  model: string;
  maxTokens: number;
  signal: AbortSignal;
  onToken: (t: string) => void;
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

/** protocol → 数据面路径。 */
const URL_BY_PROTOCOL: Record<Protocol, string> = {
  [Protocol.MESSAGES]: "/v1/messages",
  [Protocol.CHAT_COMPLETIONS]: "/v1/chat/completions",
  [Protocol.RESPONSES]: "/v1/responses",
};

export async function runTurn(
  messages: ChatTurnMsg[],
  opts: ChatTurnOpts,
): Promise<ChatTurnResult> {
  const body = buildBody(opts.fmt, messages, opts.model, opts.maxTokens);

  const base = await apiBase();
  const t0 = performance.now();
  let resp: Response;
  try {
    resp = await fetch(base + URL_BY_PROTOCOL[opts.fmt], {
      method: "POST",
      headers: { "content-type": "application/json" },
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

  const stream = new ChatStream(opts.fmt);
  const buf: string[] = [];
  let aborted = false;
  try {
    for await (const tok of stream.textDeltas(resp, opts.signal)) {
      buf.push(tok);
      opts.onToken(tok);
    }
  } catch (e) {
    if (opts.signal.aborted) {
      aborted = true;
    } else {
      throw e;
    }
  }

  return {
    text: buf.join(""),
    inputTokens: stream.inputTokens,
    outputTokens: stream.outputTokens,
    latencyMs: Math.round(performance.now() - t0),
    aborted,
  };
}

function buildBody(
  fmt: Protocol,
  messages: ChatTurnMsg[],
  model: string,
  maxTokens: number,
): Record<string, unknown> {
  if (fmt === Protocol.MESSAGES) {
    return { model, max_tokens: maxTokens, stream: true, messages };
  }
  if (fmt === Protocol.CHAT_COMPLETIONS) {
    return {
      model,
      stream: true,
      stream_options: { include_usage: true },
      max_tokens: maxTokens,
      messages,
    };
  }
  // RESPONSES:字段名是 max_output_tokens,input item 需带 type="message"
  return {
    model,
    stream: true,
    max_output_tokens: maxTokens,
    input: messages.map((m) => ({
      type: "message",
      role: m.role,
      content: m.content,
    })),
  };
}
