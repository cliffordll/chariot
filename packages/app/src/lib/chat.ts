/**
 * Chat 页核心:历史消息 → 一次 chat RPC → typed StreamEvent 回流(0.6.5 S.10 起)。
 *
 * 跟 0.5.0 SSE 路径的差异:
 * - 不再调 `/v1/messages` HTTP,改 RPC `chat(provider_name, messages, ...)`
 * - SSE 解析逻辑住 `streams.ts` 的 `runChatTurn`,这层只组 ChatRequest 调它
 */

import { runChatTurn, type ChatRequest, type StreamEvent } from "@/lib/streams";

export interface ChatTurnMsg {
  role: "user" | "assistant";
  content: string;
}

export interface ChatTurnOpts {
  /** Provider entry name(对齐 sidecar `chat.provider_name`)。 */
  provider: string;
  /** 可选 LLM model id 覆盖(per-call,详见 chat_request.py model 字段)。 */
  model?: string | null;
  /** 0.6.6+ per-call override:覆盖 entry.options.base_url。空字符串 / null = 不覆盖。 */
  baseUrl?: string | null;
  /** 0.6.6+ per-call override:覆盖 entry.options.api_key。空字符串 / null = 不覆盖。 */
  apiKey?: string | null;
  maxTokens: number;
  /**
   * Anthropic 采样参数。两者默认 1.0(等同不调);只在 ≠ 1 时才发到 body,以遵循
   * Anthropic 文档"建议两者只挑一个"的语义,避免显式传 1 干扰。
   */
  temperature?: number;
  topP?: number;
  /** 0.4.0 加。stateful 多轮 convo id;非空时 sidecar 接续历史。 */
  conversationId?: string | null;
  /** 0.7.2-tool+ agent_profile name;非空时由 AIAgent 解析 provider/prompt/tool 三件套绑定。 */
  agentProfile?: string | null;
  signal: AbortSignal;
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
  constructor(message: string) {
    super(message);
    this.name = "ChatError";
  }
}

export async function runTurn(messages: ChatTurnMsg[], opts: ChatTurnOpts): Promise<ChatTurnResult> {
  const req: ChatRequest = {
    provider_name: opts.provider,
    messages,
    max_tokens: opts.maxTokens,
  };
  if (opts.model) req.model = opts.model;
  if (opts.baseUrl) req.base_url = opts.baseUrl;
  if (opts.apiKey) req.api_key = opts.apiKey;
  if (opts.conversationId) req.conversation_id = opts.conversationId;
  if (opts.agentProfile) req.agent_profile = opts.agentProfile;
  if (opts.temperature !== undefined && opts.temperature !== 1) {
    req.temperature = opts.temperature;
  }
  if (opts.topP !== undefined && opts.topP !== 1) {
    req.top_p = opts.topP;
  }

  const t0 = performance.now();
  // 跨 turn 跟踪:current_turn_text 累积当前 assistant turn 文本;
  // turn_complete(role=assistant)snapshot 到 lastAssistantText,被下一轮覆盖,
  // 流尾留下的就是最终轮的 final 文本(返回给 caller 作 ChatTurnResult.text)。
  let currentTurnText = "";
  let lastAssistantText = "";

  const handleEvent = (ev: StreamEvent) => {
    opts.onEvent(ev);
    if (ev.kind === "text") {
      currentTurnText += ev.text;
    } else if (ev.kind === "turn_complete" && ev.role === "assistant") {
      lastAssistantText = currentTurnText;
      currentTurnText = "";
    }
  };

  try {
    const result = await runChatTurn(req, opts.signal, handleEvent);
    return {
      text: lastAssistantText,
      inputTokens: result.inputTokens,
      outputTokens: result.outputTokens,
      latencyMs: Math.round(performance.now() - t0),
      aborted: result.aborted,
    };
  } catch (e) {
    if (opts.signal.aborted) {
      return {
        text: lastAssistantText,
        inputTokens: 0,
        outputTokens: 0,
        latencyMs: Math.round(performance.now() - t0),
        aborted: true,
      };
    }
    throw new ChatError(e instanceof Error ? e.message : String(e));
  }
}
