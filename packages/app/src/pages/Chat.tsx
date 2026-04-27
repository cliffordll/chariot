import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api, type ModelEntry, type ModelsListResponse } from "@/lib/api";
import { ChatError, runTurn, type ChatTurnMsg } from "@/lib/chat";

/**
 * Chat 页(0.3.1):极简化 —— 只剩 entry 选择 + 输入框。
 *
 * sampling 参数(temperature / top_p / max_tokens)不在 Chat 页配置,从 selected
 * entry 的 `params` 字段直接读;要改去 Models 页编辑 entry 的 params。这样"参数
 * 配置"只有一个归属(entry-level),Chat 页保持轻量。
 *
 * Anthropic 默认兜底:temperature=1, top_p=1, max_tokens=1024。
 */

/** 兜底 sampling:entry.params 没设这些字段时用。 */
const DEFAULT_MAX_TOKENS = 1024;

interface SamplingValues {
  maxTokens: number;
  /** 不设(或 entry.params 没值)→ undefined,runTurn 不会发 temperature 字段。 */
  temperature: number | undefined;
  /** 同上。 */
  topP: number | undefined;
}

/** 从 entry.params 读 sampling;兜底 max_tokens=1024,temperature/top_p 缺失就不传。 */
function samplingFromEntry(entry: ModelEntry | undefined): SamplingValues {
  const p = entry?.params ?? {};
  const mt = p.max_tokens;
  const t = p.temperature;
  const tp = p.top_p;
  return {
    maxTokens: typeof mt === "number" && mt > 0 ? Math.round(mt) : DEFAULT_MAX_TOKENS,
    temperature: typeof t === "number" ? t : undefined,
    topP: typeof tp === "number" ? tp : undefined,
  };
}

/** localStorage key:per-browser 持久化"上次选了哪个 entry"。 */
const STORAGE_KEY = "chariot.chat.selected_entry";

function loadStoredEntry(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistEntry(name: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (name) window.localStorage.setItem(STORAGE_KEY, name);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* localStorage 不可用 → 忽略 */
  }
}

interface MetaInfo {
  model: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
}

type DisplayMsg =
  | { role: "user"; content: string }
  | {
      role: "assistant";
      content: string;
      meta: MetaInfo | null;
      status: "streaming" | "done" | "aborted" | "error";
      errorMsg: string | null;
    };

type ModelsState =
  | { kind: "loading" }
  | { kind: "ok"; data: ModelsListResponse }
  | { kind: "err"; message: string };

export default function Chat() {
  const [modelsState, setModelsState] = useState<ModelsState>({ kind: "loading" });
  const [selectedEntry, setSelectedEntryState] = useState<string | null>(() => loadStoredEntry());

  const [messages, setMessages] = useState<DisplayMsg[]>([]);
  const [input, setInput] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  const setSelectedEntry = useCallback((name: string | null) => {
    setSelectedEntryState(name);
    persistEntry(name);
  }, []);

  const loadModels = useCallback(async () => {
    setModelsState({ kind: "loading" });
    try {
      const models = await api.listModels();
      setModelsState({ kind: "ok", data: models });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setModelsState({ kind: "err", message: msg });
    }
  }, []);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  /** 收到 entries 后修复 selectedEntry:没选 / 选的 entry 不在 → 自动落到第一条。 */
  useEffect(() => {
    if (modelsState.kind !== "ok") return;
    const { data } = modelsState;
    if (data.available.length === 0) {
      if (selectedEntry !== null) setSelectedEntry(null);
      return;
    }
    const valid = selectedEntry !== null && data.available.includes(selectedEntry);
    if (!valid) setSelectedEntry(data.available[0]);
  }, [modelsState, selectedEntry, setSelectedEntry]);

  // auto-scroll to bottom unless user is scrolled up
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
    if (distance < 64) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const canSend = !inFlight && input.trim().length > 0 && selectedEntry !== null;

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || inFlight || !selectedEntry) return;
    setInput("");

    const nextMsgs: DisplayMsg[] = [
      ...messages,
      { role: "user", content: text },
      {
        role: "assistant",
        content: "",
        meta: null,
        status: "streaming",
        errorMsg: null,
      },
    ];
    setMessages(nextMsgs);
    setInFlight(true);

    const history: ChatTurnMsg[] = nextMsgs.flatMap<ChatTurnMsg>((m) => {
      if (m.role === "user") return [{ role: "user", content: m.content }];
      if (m.content) return [{ role: "assistant", content: m.content }];
      return [];
    });

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    // 发请求前从当前 entry 现取 sampling;每次发都读最新值,Models 页改了
    // params 后下一条消息立刻生效。
    const entry =
      modelsState.kind === "ok"
        ? modelsState.data.entries.find((e) => e.name === selectedEntry)
        : undefined;
    const sampling = samplingFromEntry(entry);

    try {
      const result = await runTurn(history, {
        model: selectedEntry,
        maxTokens: sampling.maxTokens,
        temperature: sampling.temperature,
        topP: sampling.topP,
        signal: ctrl.signal,
        onToken: (tok) => {
          setMessages((cur) => {
            const copy = cur.slice();
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant" && last.status === "streaming") {
              copy[copy.length - 1] = { ...last, content: last.content + tok };
            }
            return copy;
          });
        },
      });

      setMessages((cur) => {
        const copy = cur.slice();
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant") {
          copy[copy.length - 1] = {
            ...last,
            status: result.aborted ? "aborted" : "done",
            meta: {
              model: selectedEntry,
              inputTokens: result.inputTokens,
              outputTokens: result.outputTokens,
              latencyMs: result.latencyMs,
            },
          };
        }
        return copy;
      });
    } catch (e) {
      const msg =
        e instanceof ChatError ? `HTTP ${e.status}: ${e.body.slice(0, 300)}` : extractErr(e);
      setMessages((cur) => {
        const copy = cur.slice();
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant") {
          copy[copy.length - 1] = { ...last, status: "error", errorMsg: msg };
        }
        return copy;
      });
    } finally {
      setInFlight(false);
      abortRef.current = null;
    }
  }, [input, inFlight, messages, selectedEntry, modelsState]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleNewChat = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
  }, []);

  /**
   * error / aborted 后点 Retry:
   * 1. 从 messages 末尾剥掉最近一对 [user, failed assistant]
   * 2. 把 user.content 回填 input
   * 用户按 Send 完成重试;不强行自动发,避免"黑盒重试"对用户难追踪
   */
  const handleRetry = useCallback(() => {
    if (inFlight) return;
    const copy = messages.slice();
    let failedIdx = -1;
    for (let i = copy.length - 1; i >= 0; i--) {
      if (copy[i].role === "assistant") {
        failedIdx = i;
        break;
      }
    }
    if (failedIdx < 1) return;
    const userMsg = copy[failedIdx - 1];
    if (userMsg.role !== "user") return;
    setMessages(copy.slice(0, failedIdx - 1));
    setInput(userMsg.content);
  }, [messages, inFlight]);

  return (
    <section className="flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Chat</h1>
        <Button variant="outline" size="sm" onClick={handleNewChat}>
          New chat
        </Button>
      </div>

      <EntryRow
        modelsState={modelsState}
        selectedEntry={selectedEntry}
        onSelect={setSelectedEntry}
      />

      <div
        ref={scrollRef}
        className="mb-3 flex-1 overflow-y-auto rounded-lg border border-border bg-muted/20 p-4"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">输入消息开始对话;流式逐 token 渲染。</p>
        ) : (
          <ul className="space-y-4">
            {messages.map((m, i) => {
              const isLast = i === messages.length - 1;
              const canRetry =
                isLast &&
                m.role === "assistant" &&
                (m.status === "error" || m.status === "aborted");
              return (
                <li key={i}>
                  <MessageBubble msg={m} onRetry={canRetry ? handleRetry : undefined} />
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="flex gap-2">
        <Textarea
          value={input}
          placeholder="发消息…(Enter 发送,Shift+Enter 换行)"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) void handleSend();
            }
          }}
          disabled={inFlight}
          className="min-h-20 flex-1"
        />
        {inFlight ? (
          <Button variant="destructive" onClick={handleStop}>
            Stop
          </Button>
        ) : (
          <Button onClick={() => void handleSend()} disabled={!canSend}>
            Send
          </Button>
        )}
      </div>
    </section>
  );
}

function EntryRow({
  modelsState,
  selectedEntry,
  onSelect,
}: {
  modelsState: ModelsState;
  selectedEntry: string | null;
  onSelect: (name: string) => void;
}) {
  if (modelsState.kind === "loading") {
    return <div className="mb-3 text-xs text-muted-foreground">读取 entries…</div>;
  }

  if (modelsState.kind === "err") {
    return (
      <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        无法读取 entries:{modelsState.message}
      </div>
    );
  }

  const { data } = modelsState;

  if (data.available.length === 0) {
    return (
      <div className="mb-3 rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        DB 里没有 entry。在 Models 页 <code className="rounded bg-muted px-1 py-0.5">+ Add</code>{" "}
        新建一条后再来发消息(client 必须在 body.model 写 entry name 才能路由到上游)。
      </div>
    );
  }

  return (
    <div className="mb-3 rounded-md border border-border bg-muted/10 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">model</span>
        <Select value={selectedEntry ?? undefined} onValueChange={onSelect}>
          <SelectTrigger className="h-8 w-56">
            <SelectValue placeholder="选 entry" />
          </SelectTrigger>
          <SelectContent>
            {data.available.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="ml-auto text-xs text-muted-foreground">
          sampling 参数随 entry.params 走;改去 Models 页编辑该 entry。
        </span>
      </div>
    </div>
  );
}

function MessageBubble({ msg, onRetry }: { msg: DisplayMsg; onRetry?: () => void }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground whitespace-pre-wrap">
          {msg.content}
        </div>
      </div>
    );
  }

  const isStreaming = msg.status === "streaming";
  return (
    <div className="flex flex-col items-start gap-1">
      <div className="max-w-[85%] rounded-lg border border-border bg-background px-3 py-2 text-sm whitespace-pre-wrap">
        {msg.content || (isStreaming ? <span className="text-muted-foreground">…</span> : null)}
        {msg.status === "aborted" && (
          <span className="ml-1 text-xs text-muted-foreground">[已中断]</span>
        )}
      </div>
      {msg.status === "error" && msg.errorMsg && (
        <div className="max-w-[85%] rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1 text-xs text-destructive">
          {msg.errorMsg}
        </div>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          Retry(回填输入框,按 Send 重发)
        </button>
      )}
      {msg.meta && <MetaLine meta={msg.meta} />}
    </div>
  );
}

function MetaLine({ meta }: { meta: MetaInfo }) {
  const parts = [
    meta.model,
    `${meta.inputTokens}→${meta.outputTokens} tok`,
    `${meta.latencyMs} ms`,
  ];
  return <div className="text-xs text-muted-foreground font-mono">[{parts.join(" · ")}]</div>;
}

function extractErr(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
