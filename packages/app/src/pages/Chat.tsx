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
import {
  ApiError,
  api,
  type AnthropicBlock,
  type Conversation,
  type Message,
  type ModelEntry,
  type ModelsListResponse,
} from "@/lib/api";
import { ChatError, runTurn, type ChatTurnMsg } from "@/lib/chat";

/**
 * Chat 页(0.4.0):左侧 conversation 侧栏 + 右侧 messages 视图。
 *
 * - sampling 参数取自 selected entry 的 `params`(不在本页配置)
 * - "+ New" 进入 draft 状态(conversationId=null);首次发送时创建 conversation
 * - 选中已存在 conversation → 拉取 messages,渲染 anthropic content blocks
 * - 发送只发新增的 user message(body.messages 单条);server 自己 prepend 历史
 *
 * Anthropic 默认兜底:max_tokens=1024,temperature/top_p 缺省不发。
 */

const DEFAULT_MAX_TOKENS = 1024;

interface SamplingValues {
  maxTokens: number;
  temperature: number | undefined;
  topP: number | undefined;
}

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

const ENTRY_STORAGE_KEY = "chariot.chat.selected_entry";
const CONV_STORAGE_KEY = "chariot.chat.active_conversation";

function lsGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function lsSet(key: string, value: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

interface PendingTurn {
  /** 用户这轮发的文本(已显示在右侧但还没落 DB)。 */
  userText: string;
  /** assistant 流式增量文本。 */
  assistantText: string;
  status: "streaming" | "done" | "aborted" | "error";
  errorMsg: string | null;
  meta: { inputTokens: number; outputTokens: number; latencyMs: number; model: string } | null;
}

type ConvPaneState =
  | { kind: "draft" }
  | { kind: "loading"; id: string }
  | { kind: "loaded"; id: string; conversation: Conversation; messages: Message[] }
  | { kind: "err"; id: string; message: string };

type ModelsState =
  | { kind: "loading" }
  | { kind: "ok"; data: ModelsListResponse }
  | { kind: "err"; message: string };

export default function Chat() {
  const [modelsState, setModelsState] = useState<ModelsState>({ kind: "loading" });
  const [selectedEntry, setSelectedEntryState] = useState<string | null>(() =>
    lsGet(ENTRY_STORAGE_KEY),
  );

  const [convs, setConvs] = useState<Conversation[]>([]);
  const [convsLoading, setConvsLoading] = useState(true);
  const [convsErr, setConvsErr] = useState<string | null>(null);

  const [pane, setPane] = useState<ConvPaneState>(() => {
    const id = lsGet(CONV_STORAGE_KEY);
    return id ? { kind: "loading", id } : { kind: "draft" };
  });
  const [pending, setPending] = useState<PendingTurn | null>(null);

  const [input, setInput] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const setSelectedEntry = useCallback((name: string | null) => {
    setSelectedEntryState(name);
    lsSet(ENTRY_STORAGE_KEY, name);
  }, []);

  const setActivePane = useCallback((next: ConvPaneState) => {
    setPane(next);
    if (next.kind === "draft") lsSet(CONV_STORAGE_KEY, null);
    else lsSet(CONV_STORAGE_KEY, next.id);
  }, []);

  const loadModels = useCallback(async () => {
    setModelsState({ kind: "loading" });
    try {
      const data = await api.listModels();
      setModelsState({ kind: "ok", data });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setModelsState({ kind: "err", message: msg });
    }
  }, []);

  const loadConvs = useCallback(async () => {
    setConvsLoading(true);
    setConvsErr(null);
    try {
      const r = await api.listConversations({ limit: 100 });
      setConvs(r.items);
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setConvsErr(msg);
    } finally {
      setConvsLoading(false);
    }
  }, []);

  const loadConvDetail = useCallback(async (id: string) => {
    setActivePane({ kind: "loading", id });
    try {
      const detail = await api.getConversation(id);
      setActivePane({
        kind: "loaded",
        id,
        conversation: detail.conversation,
        messages: detail.messages,
      });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setActivePane({ kind: "err", id, message: msg });
    }
  }, [setActivePane]);

  // initial loads
  useEffect(() => {
    void loadModels();
    void loadConvs();
  }, [loadModels, loadConvs]);

  // 启动时若 localStorage 里残留了 conv id,拉一次详情
  useEffect(() => {
    if (pane.kind === "loading") {
      void loadConvDetail(pane.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // entry 兜底:无效 → 落到第一条
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

  // auto-scroll:每次 pane / pending 变化都吸到底,确保最新消息可见。
  // 之前用 64px 阈值条件式滚动,但 turn 结束后 loadConvDetail 重拉 canonical
  // messages 时长度跳变,distance 常超 64px → 阈值卡住不滚 → 用户看不到最新。
  // 牺牲"流式中往上翻看历史不被打断"的便利,优先保证消息可见性。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [pane, pending]);

  const startNewChat = useCallback(() => {
    abortRef.current?.abort();
    setPending(null);
    setActivePane({ kind: "draft" });
  }, [setActivePane]);

  const selectConv = useCallback(
    (id: string) => {
      if (pane.kind !== "draft" && pane.kind !== "loading" && pane.kind !== "err") {
        if (pane.id === id) return;
      }
      abortRef.current?.abort();
      setPending(null);
      void loadConvDetail(id);
    },
    [pane, loadConvDetail],
  );

  const handleDeleteConv = useCallback(
    async (id: string) => {
      if (!window.confirm("删除这个对话?不可撤销。")) return;
      try {
        await api.deleteConversation(id);
      } catch (e) {
        const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
        alert(`删除失败: ${msg}`);
        return;
      }
      // 当前选中被删 → 回 draft
      if (
        (pane.kind === "loaded" || pane.kind === "loading" || pane.kind === "err") &&
        pane.id === id
      ) {
        setPending(null);
        setActivePane({ kind: "draft" });
      }
      void loadConvs();
    },
    [pane, setActivePane, loadConvs],
  );

  const handleRenameConv = useCallback(
    async (id: string, currentTitle: string | null) => {
      const next = window.prompt("新标题(空字符串清空):", currentTitle ?? "");
      if (next === null) return;
      const title = next.trim() === "" ? null : next.trim();
      try {
        await api.updateConversationTitle(id, title);
      } catch (e) {
        const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
        alert(`改名失败: ${msg}`);
        return;
      }
      void loadConvs();
      if (pane.kind === "loaded" && pane.id === id) {
        void loadConvDetail(id);
      }
    },
    [loadConvs, pane, loadConvDetail],
  );

  const canSend =
    !inFlight &&
    input.trim().length > 0 &&
    selectedEntry !== null &&
    (pane.kind === "draft" || pane.kind === "loaded");

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || inFlight || !selectedEntry) return;
    if (pane.kind !== "draft" && pane.kind !== "loaded") return;

    setInput("");
    setInFlight(true);

    // 0. 若 draft → 先创建 conversation
    let convId: string;
    if (pane.kind === "draft") {
      try {
        const conv = await api.createConversation({});
        convId = conv.id;
        setActivePane({
          kind: "loaded",
          id: conv.id,
          conversation: conv,
          messages: [],
        });
      } catch (e) {
        const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
        setPending({
          userText: text,
          assistantText: "",
          status: "error",
          errorMsg: `创建会话失败: ${msg}`,
          meta: null,
        });
        setInFlight(false);
        return;
      }
      void loadConvs();
    } else {
      convId = pane.id;
    }

    // 1. 显示 pending 行
    setPending({
      userText: text,
      assistantText: "",
      status: "streaming",
      errorMsg: null,
      meta: null,
    });

    // 2. 取 sampling
    const entry =
      modelsState.kind === "ok"
        ? modelsState.data.entries.find((e) => e.name === selectedEntry)
        : undefined;
    const sampling = samplingFromEntry(entry);

    // 3. 只发新的 user message;server 会从 DB prepend 历史
    const newMessages: ChatTurnMsg[] = [{ role: "user", content: text }];

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const result = await runTurn(newMessages, {
        model: selectedEntry,
        maxTokens: sampling.maxTokens,
        temperature: sampling.temperature,
        topP: sampling.topP,
        conversationId: convId,
        signal: ctrl.signal,
        onToken: (tok) => {
          setPending((cur) =>
            cur
              ? { ...cur, assistantText: cur.assistantText + tok }
              : cur,
          );
        },
      });

      setPending((cur) =>
        cur
          ? {
              ...cur,
              status: result.aborted ? "aborted" : "done",
              meta: {
                inputTokens: result.inputTokens,
                outputTokens: result.outputTokens,
                latencyMs: result.latencyMs,
                model: selectedEntry,
              },
            }
          : cur,
      );

      // 4. 成功 → 重拉 canonical messages(含 tool_use/tool_result blocks)
      if (!result.aborted) {
        await loadConvDetail(convId);
        void loadConvs();
        setPending(null);
      }
    } catch (e) {
      const msg =
        e instanceof ChatError ? `HTTP ${e.status}: ${e.body.slice(0, 300)}` : extractErr(e);
      setPending((cur) =>
        cur ? { ...cur, status: "error", errorMsg: msg } : cur,
      );
    } finally {
      setInFlight(false);
      abortRef.current = null;
    }
  }, [input, inFlight, selectedEntry, pane, modelsState, setActivePane, loadConvDetail, loadConvs]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleRetry = useCallback(() => {
    if (inFlight || !pending) return;
    if (pending.status !== "error" && pending.status !== "aborted") return;
    const text = pending.userText;
    setPending(null);
    setInput(text);
  }, [inFlight, pending]);

  const activeId =
    pane.kind === "loaded" || pane.kind === "loading" || pane.kind === "err" ? pane.id : null;

  return (
    <section className="flex h-full gap-3">
      {/* 左侧 sidebar */}
      <aside className="flex w-64 flex-col rounded-lg border border-border bg-muted/10">
        <div className="flex items-center justify-between border-b border-border p-2">
          <h2 className="text-xs uppercase tracking-wide text-muted-foreground">
            conversations
          </h2>
          <div className="flex items-center gap-1">
            <Button size="sm" className="h-6 px-2 text-xs" onClick={startNewChat}>
              + New
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => void loadConvs()}
            >
              ⟳
            </Button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {convsLoading && (
            <p className="p-3 text-xs text-muted-foreground">Loading…</p>
          )}
          {convsErr && (
            <p className="p-3 text-xs text-destructive">{convsErr}</p>
          )}
          {!convsLoading && convs.length === 0 && !convsErr && (
            <p className="p-3 text-xs text-muted-foreground">
              还没有对话。点 + New 开始第一个。
            </p>
          )}
          <ul className="divide-y divide-border">
            {convs.map((c) => {
              const isActive = activeId === c.id;
              return (
                <li
                  key={c.id}
                  className={
                    "group flex cursor-pointer items-start gap-1 px-2 py-2 text-xs hover:bg-muted/30 " +
                    (isActive ? "bg-muted/40" : "")
                  }
                  onClick={() => selectConv(c.id)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">
                      {c.title?.trim() ? c.title : <span className="text-muted-foreground">(no title)</span>}
                    </div>
                    <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                      <code className="font-mono">{c.id.slice(-6)}</code>
                      {" · "}
                      {c.message_count} msg
                      {c.last_model && ` · ${c.last_model}`}
                    </div>
                  </div>
                  <div className="flex flex-col gap-0.5 opacity-0 group-hover:opacity-100">
                    <button
                      type="button"
                      className="text-[10px] text-muted-foreground hover:text-foreground"
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleRenameConv(c.id, c.title);
                      }}
                      title="rename"
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      className="text-[10px] text-muted-foreground hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleDeleteConv(c.id);
                      }}
                      title="delete"
                    >
                      ×
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </aside>

      {/* 右侧主面板 */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="mb-3 flex items-center justify-between">
          <h1 className="text-2xl font-semibold">
            {pane.kind === "loaded" && pane.conversation.title
              ? pane.conversation.title
              : pane.kind === "draft"
                ? "New chat"
                : "Chat"}
          </h1>
          {pane.kind === "loaded" && (
            <div className="text-xs text-muted-foreground">
              <code className="font-mono">{pane.id}</code>
            </div>
          )}
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
          <MessagesView pane={pane} pending={pending} onRetry={handleRetry} />
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
      </div>
    </section>
  );
}

// ---------- EntryRow(沿用 0.3.1)----------

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
        DB 里没有 entry。在 Models 页 + Add 新建一条后再来发消息。
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

// ---------- MessagesView ----------

function MessagesView({
  pane,
  pending,
  onRetry,
}: {
  pane: ConvPaneState;
  pending: PendingTurn | null;
  onRetry: () => void;
}) {
  if (pane.kind === "draft") {
    return (
      <div className="text-sm text-muted-foreground">
        {pending ? null : "新对话:输入消息,首发时自动创建 conversation。"}
        {pending && <PendingBubble pending={pending} onRetry={onRetry} />}
      </div>
    );
  }
  if (pane.kind === "loading") {
    return <p className="text-sm text-muted-foreground">Loading conversation…</p>;
  }
  if (pane.kind === "err") {
    return (
      <p className="text-sm text-destructive">无法读取对话:{pane.message}</p>
    );
  }
  const { messages } = pane;
  if (messages.length === 0 && !pending) {
    return (
      <p className="text-sm text-muted-foreground">
        空对话:输入消息开始。
      </p>
    );
  }
  return (
    <ul className="space-y-4">
      {messages.map((m) => (
        <li key={m.seq}>
          <MessageRow msg={m} />
        </li>
      ))}
      {pending && (
        <li>
          <PendingBubble pending={pending} onRetry={onRetry} />
        </li>
      )}
    </ul>
  );
}

function MessageRow({ msg }: { msg: Message }) {
  // user 单纯文本(string)→ 浅色气泡;否则按 blocks 渲染
  if (msg.role === "user" && typeof msg.content === "string") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
          {msg.content}
        </div>
      </div>
    );
  }

  // user blocks(通常是 tool_result)
  if (msg.role === "user") {
    return (
      <div className="flex flex-col items-start gap-1">
        <BlocksRender blocks={asBlocks(msg.content)} side="tool" />
      </div>
    );
  }

  // assistant
  return (
    <div className="flex flex-col items-start gap-1">
      {typeof msg.content === "string" ? (
        <AssistantTextBubble text={msg.content} />
      ) : (
        <BlocksRender blocks={asBlocks(msg.content)} side="assistant" />
      )}
      {msg.model_name && (
        <div className="font-mono text-xs text-muted-foreground">
          [{msg.model_name}]
        </div>
      )}
    </div>
  );
}

function asBlocks(content: string | AnthropicBlock[]): AnthropicBlock[] {
  if (typeof content === "string") return [{ type: "text", text: content }];
  return content;
}

function AssistantTextBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-border bg-background px-3 py-2 text-sm">
      {text || <span className="text-muted-foreground">(empty)</span>}
    </div>
  );
}

function BlocksRender({
  blocks,
  side,
}: {
  blocks: AnthropicBlock[];
  side: "assistant" | "tool";
}) {
  return (
    <div className="flex w-full flex-col gap-2">
      {blocks.map((b, i) => (
        <BlockRender key={i} block={b} side={side} />
      ))}
    </div>
  );
}

function BlockRender({
  block,
  side,
}: {
  block: AnthropicBlock;
  side: "assistant" | "tool";
}) {
  if (block.type === "text" && typeof (block as { text?: unknown }).text === "string") {
    const text = (block as { text: string }).text;
    return <AssistantTextBubble text={text} />;
  }

  if (block.type === "tool_use") {
    const tu = block as Extract<AnthropicBlock, { type: "tool_use" }>;
    return (
      <div className="max-w-[85%] rounded-lg border border-blue-300/50 bg-blue-50 p-2 text-xs dark:border-blue-700/50 dark:bg-blue-950/30">
        <div className="mb-1 flex items-center gap-2">
          <span className="rounded bg-blue-200 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-blue-900 dark:bg-blue-800 dark:text-blue-100">
            tool_use
          </span>
          <code className="font-mono">{tu.name}</code>
          <code className="ml-auto font-mono text-[10px] text-muted-foreground">
            {tu.id.slice(-8)}
          </code>
        </div>
        <pre className="max-h-40 overflow-auto rounded bg-background/60 p-1.5 font-mono text-[11px] leading-snug">
          {JSON.stringify(tu.input, null, 2)}
        </pre>
      </div>
    );
  }

  if (block.type === "tool_result") {
    const tr = block as Extract<AnthropicBlock, { type: "tool_result" }>;
    const isErr = tr.is_error === true;
    return (
      <div
        className={
          "max-w-[85%] rounded-lg border p-2 text-xs " +
          (isErr
            ? "border-destructive/40 bg-destructive/5"
            : "border-emerald-300/50 bg-emerald-50 dark:border-emerald-700/50 dark:bg-emerald-950/30")
        }
      >
        <div className="mb-1 flex items-center gap-2">
          <span
            className={
              "rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide " +
              (isErr
                ? "bg-destructive/20 text-destructive"
                : "bg-emerald-200 text-emerald-900 dark:bg-emerald-800 dark:text-emerald-100")
            }
          >
            tool_result {isErr && "· error"}
          </span>
          <code className="ml-auto font-mono text-[10px] text-muted-foreground">
            {tr.tool_use_id.slice(-8)}
          </code>
        </div>
        <ToolResultContent content={tr.content} />
      </div>
    );
  }

  // 兜底:其它 type → JSON dump
  void side;
  return (
    <pre className="max-w-[85%] overflow-auto rounded border border-border bg-muted/30 p-2 font-mono text-[11px]">
      {JSON.stringify(block, null, 2)}
    </pre>
  );
}

function ToolResultContent({
  content,
}: {
  content: string | AnthropicBlock[];
}) {
  if (typeof content === "string") {
    return (
      <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded bg-background/60 p-1.5 text-[11px] leading-snug">
        {content}
      </pre>
    );
  }
  return (
    <div className="space-y-1">
      {content.map((b, i) => {
        if (b.type === "text" && typeof (b as { text?: unknown }).text === "string") {
          return (
            <pre
              key={i}
              className="max-h-60 overflow-auto whitespace-pre-wrap rounded bg-background/60 p-1.5 text-[11px] leading-snug"
            >
              {(b as { text: string }).text}
            </pre>
          );
        }
        return (
          <pre
            key={i}
            className="overflow-auto rounded bg-background/60 p-1.5 font-mono text-[11px]"
          >
            {JSON.stringify(b, null, 2)}
          </pre>
        );
      })}
    </div>
  );
}

function PendingBubble({
  pending,
  onRetry,
}: {
  pending: PendingTurn;
  onRetry: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
          {pending.userText}
        </div>
      </div>
      <div className="flex flex-col items-start gap-1">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-border bg-background px-3 py-2 text-sm">
          {pending.assistantText ||
            (pending.status === "streaming" ? (
              <span className="text-muted-foreground">…</span>
            ) : null)}
          {pending.status === "aborted" && (
            <span className="ml-1 text-xs text-muted-foreground">[已中断]</span>
          )}
        </div>
        {pending.status === "error" && pending.errorMsg && (
          <div className="max-w-[85%] rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1 text-xs text-destructive">
            {pending.errorMsg}
          </div>
        )}
        {(pending.status === "error" || pending.status === "aborted") && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={onRetry}
          >
            Retry(回填输入框)
          </Button>
        )}
        {pending.meta && (
          <div className="font-mono text-xs text-muted-foreground">
            [{pending.meta.model} · {pending.meta.inputTokens}→{pending.meta.outputTokens} tok ·{" "}
            {pending.meta.latencyMs} ms]
          </div>
        )}
      </div>
    </div>
  );
}

function extractErr(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
