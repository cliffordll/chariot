/**
 * Claude 形态 ChatEvent → typed StreamEvent + usage 累加(0.6.5 S.10 起)。
 *
 * 跟 0.5.0 SSE 路径的差异:
 * - 数据源不再是 `Response` SSE 流,改成 Tauri event `rpc_notify`(method='chat_event'
 *   的 notify 帧)— 由 Rust JsonRpcClient 路由 sidecar stdout
 * - payload 是 `dataclasses.asdict(ChatEvent)`,字段都是 snake_case
 *   (`kind` / `message` / `content_block` / `delta` / `error_type` 等)
 *
 * 业务层(chat.ts)用 `consumeChatStream(callId, signal, onEvent)` 订阅一次 chat,
 * 内部:
 * 1. `event.listen("rpc_notify", handler)` 注册全局监听
 * 2. 调 `rpc("chat", ...)` 启 chat;返 `{stream_id, ended_at}` 表示流结束
 * 3. `kind === "stream_done"` 或 chat response 收到时整个 chat 收尾
 */

import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { rpc } from "@/lib/api";

// ============================================================
// Claude 形态 ChatEvent payload(对齐 chariot.agent.chat_event.ChatEvent)
// ============================================================

/** notify 帧的 payload(`{"method":"chat_event","params":<ChatEvent>}`)。 */
interface ChatEventNotify {
  method: string;
  params: ChatEventPayload;
}

interface ChatEventPayload {
  kind: string;
  message?: {
    id?: string;
    role?: string;
    model?: string;
    usage?: { input_tokens?: number; output_tokens?: number } | null;
  } | null;
  index?: number | null;
  content_block?: {
    type?: string;
    text?: string;
    id?: string;
    name?: string;
    tool_use_id?: string;
    content?: unknown;
    is_error?: boolean;
  } | null;
  delta?: {
    type?: string;
    text?: string;
    partial_json?: string;
    stop_reason?: string;
  } | null;
  usage?: { input_tokens?: number; output_tokens?: number } | null;
  error_type?: string | null;
  error_message?: string | null;
  tool_use_id?: string | null;
  content?: unknown;
  is_error?: boolean | null;
}

// ============================================================
// 上层 typed events
// ============================================================

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
  toolResultContent: string;
  isError: boolean;
}

export interface TurnCompleteEvent {
  kind: "turn_complete";
  role: string;
  inputTokens: number;
  outputTokens: number;
}

export interface StreamDoneEvent {
  kind: "stream_done";
  inputTokens: number;
  outputTokens: number;
}

export interface ErrorEvent {
  kind: "error";
  errorType: string;
  errorMessage: string;
}

export type StreamEvent =
  | TextEvent
  | ToolUseEvent
  | ToolResultEvent
  | TurnCompleteEvent
  | StreamDoneEvent
  | ErrorEvent;

/**
 * 流中途收到的 ChatEvent(kind=error)— sidecar 那侧由 Provider 抛 ProviderError
 * 或流中途 IO 错产生(契约见 chariot/docs/DESIGN.md §6.4.2)。
 *
 * runChatTurn 收到 error event 时不立刻终止流(仍 await chat RPC 返 stream_id),
 * 但等 RPC resolve 后会 throw 一个 ChatStreamError,让上层 catch 路径接管 —
 * 否则 success path 跑下去会把 pending 抹掉,用户看不到任何错误提示。
 */
export class ChatStreamError extends Error {
  errorType: string;
  errorMessage: string;
  constructor(errorType: string, errorMessage: string) {
    super(`${errorType}: ${errorMessage}`);
    this.name = "ChatStreamError";
    this.errorType = errorType;
    this.errorMessage = errorMessage;
  }
}

// ============================================================
// chat 流消费
// ============================================================

/**
 * `chat` RPC 请求体(对齐 sidecar `_RequestDecoder.parse`)。
 *
 * 必填:`provider_ref` + `messages`;可选:`model` / `max_tokens` / `conversation_id` 等。
 */
export interface ChatRequest {
  provider_ref: string | null;
  messages: Array<{ role: "user" | "assistant"; content: string | unknown[] }>;
  model?: string | null;
  max_tokens?: number;
  conversation_id?: string | null;
  agent_profile?: string | null;
  temperature?: number | null;
  top_p?: number | null;
  [key: string]: unknown;
}

export interface ChatRunResult {
  /** sidecar 返的 `stream_id`(uuid4 hex)。 */
  streamId: string;
  /** sidecar 流结束时间(epoch float seconds)。 */
  endedAt: number;
  inputTokens: number;
  outputTokens: number;
  /** 中途调 signal.abort 时为 true(此时 streamId / endedAt 仍合法,但流可能未完整)。 */
  aborted: boolean;
}

/**
 * 跑一次 chat,流式 dispatch StreamEvent + 等 RPC response 收尾。
 *
 * 实现:
 * 1. `listen("rpc_notify", ...)` 注册一个 listener,过滤 method='chat_event'
 *    的 notify,转 typed StreamEvent → 调 onEvent
 * 2. `rpc("chat", req)` await 直到 sidecar 返 response(`{stream_id, ended_at}`)
 * 3. 收到 response → unlisten + 返 ChatRunResult
 *
 * 中途调 signal.abort:停 listener + 返 aborted=true(注:**不能取消已 in-flight
 * 的 sidecar chat**,因为 sidecar 不支持 chat.cancel,0.7.0 ROADMAP);后续
 * notify 帧仍会到来但 listener 已 detach,无副作用。
 */
export async function runChatTurn(
  req: ChatRequest,
  signal: AbortSignal,
  onEvent: (ev: StreamEvent) => void,
): Promise<ChatRunResult> {
  const tracker = new ChatStreamTracker();
  // 流中途若收到 ChatEvent(kind=error),记下首条;chat RPC resolve 后转成
  // ChatStreamError 抛出,走上层 catch 路径(否则 success path 会 setPending(null)
  // 把刚 streaming 出的错误信息抹掉,前端看起来"消息发出去后无反应")。
  let streamErr: ErrorEvent | null = null;
  let frameCount = 0;
  const unlisten = await listen<ChatEventNotify>("rpc_notify", (e) => {
    if (e.payload.method !== "chat_event") return;
    if (signal.aborted) return;
    frameCount += 1;
    // 诊断日志:streaming 卡顿调试用。在 DevTools Console 过滤 [chariot] 看
    // 帧到达情况;0.6.6+ 改 Tauri Channel 后可删
    console.debug("[chariot] chat_event frame", frameCount, e.payload.params.kind);
    for (const stream_ev of tracker.consume(e.payload.params)) {
      if (stream_ev.kind === "error" && streamErr === null) {
        streamErr = stream_ev;
      }
      onEvent(stream_ev);
    }
  });
  signal.addEventListener("abort", () => unlisten());
  console.debug("[chariot] chat invoke start", req.provider_ref, req.conversation_id ?? "(stateless)");
  try {
    const result = await rpc<{ stream_id: string; ended_at: number }>("chat", req);
    console.debug("[chariot] chat resolved", { frames: frameCount, ...result });
    if (streamErr !== null) {
      const err = streamErr as ErrorEvent;
      throw new ChatStreamError(err.errorType, err.errorMessage);
    }
    return {
      streamId: result.stream_id,
      endedAt: result.ended_at,
      inputTokens: tracker.inputTokens,
      outputTokens: tracker.outputTokens,
      aborted: false,
    };
  } catch (e) {
    console.debug("[chariot] chat threw", { frames: frameCount, error: e });
    throw e;
  } finally {
    unlisten();
  }
}

/**
 * 单个 chat 流的状态机:维护跨 turn 的 usage 累加 + content block 内部状态。
 *
 * 用 `class` 而非散函数,因状态需要跨多个 ChatEvent 维持(tool_use input 增量
 * 拼接、role 跨 message_start 切换等)。
 */
class ChatStreamTracker {
  inputTokens = 0;
  outputTokens = 0;

  private _currentRole = "assistant";
  private _turnInputTokens = 0;
  private _turnOutputTokens = 0;
  private _inToolUse = false;
  private _tuId = "";
  private _tuName = "";
  private _tuInputBuf = "";
  private _inToolResult = false;
  private _trId = "";
  private _trContentBuf = "";
  private _trIsError = false;

  /** 接受一个 ChatEvent payload,产 0..N 个上层 StreamEvent。 */
  *consume(payload: ChatEventPayload): Generator<StreamEvent> {
    const kind = payload.kind;

    if (kind === "message_start") {
      const msg = payload.message;
      this._currentRole = msg?.role ?? "assistant";
      this._turnInputTokens = msg?.usage?.input_tokens ?? 0;
      this._turnOutputTokens = msg?.usage?.output_tokens ?? 0;
      return;
    }

    if (kind === "content_block_start") {
      const block = payload.content_block;
      const btype = block?.type;
      if (btype === "tool_use") {
        this._inToolUse = true;
        this._tuId = block?.id ?? "";
        this._tuName = block?.name ?? "";
        this._tuInputBuf = "";
      } else if (btype === "tool_result") {
        this._inToolResult = true;
        this._trId = block?.tool_use_id ?? "";
        this._trContentBuf = extractToolResultText(block?.content);
        this._trIsError = block?.is_error === true;
      }
      return;
    }

    if (kind === "content_block_delta") {
      const delta = payload.delta;
      const dtype = delta?.type;
      if (dtype === "text_delta" && typeof delta?.text === "string" && delta.text) {
        yield { kind: "text", text: delta.text };
      } else if (dtype === "input_json_delta" && this._inToolUse) {
        const pj = delta?.partial_json;
        if (typeof pj === "string") this._tuInputBuf += pj;
      }
      return;
    }

    if (kind === "content_block_stop") {
      if (this._inToolUse) {
        let parsedInput: Record<string, unknown> = {};
        try {
          const parsed: unknown = JSON.parse(this._tuInputBuf || "{}");
          if (isObj(parsed)) parsedInput = parsed;
        } catch {
          parsedInput = {};
        }
        yield {
          kind: "tool_use",
          toolUseId: this._tuId,
          toolName: this._tuName,
          toolInput: parsedInput,
        };
        this._inToolUse = false;
        this._tuId = "";
        this._tuName = "";
        this._tuInputBuf = "";
      } else if (this._inToolResult) {
        yield {
          kind: "tool_result",
          toolUseId: this._trId,
          toolResultContent: this._trContentBuf,
          isError: this._trIsError,
        };
        this._inToolResult = false;
        this._trId = "";
        this._trContentBuf = "";
        this._trIsError = false;
      }
      return;
    }

    if (kind === "message_delta") {
      const ot = payload.usage?.output_tokens;
      if (typeof ot === "number") this._turnOutputTokens = ot;
      return;
    }

    if (kind === "message_stop") {
      yield {
        kind: "turn_complete",
        role: this._currentRole,
        inputTokens: this._turnInputTokens,
        outputTokens: this._turnOutputTokens,
      };
      // 仅 assistant turn 累加(synthetic user 的 tool_result usage 都是 0)
      if (this._currentRole === "assistant") {
        this.inputTokens += this._turnInputTokens;
        this.outputTokens += this._turnOutputTokens;
      }
      this._turnInputTokens = 0;
      this._turnOutputTokens = 0;
      return;
    }

    if (kind === "stream_done") {
      yield {
        kind: "stream_done",
        inputTokens: this.inputTokens,
        outputTokens: this.outputTokens,
      };
      return;
    }

    if (kind === "error") {
      yield {
        kind: "error",
        errorType: payload.error_type ?? "unknown",
        errorMessage: payload.error_message ?? "",
      };
      return;
    }

    // 其它 kind(自定义 / 协议未来扩展)忽略
  }
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
// 让 ESLint / tsc 在生产 unused 探测里别误删 UnlistenFn(它是 listen 返值类型注解)
export type { UnlistenFn };
