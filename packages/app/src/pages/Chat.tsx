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
import { ApiError, api, type ModelsListResponse } from "@/lib/api";
import { ChatError, runTurn, type ChatTurnMsg } from "@/lib/chat";

const FALLBACK_MODEL_LABEL = "(server)";
const MAX_TOKENS = 1024;

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

/**
 * `models.active` 是 server 端切换用的友好名(配置文件 `[[models]] name`),
 * 在 MockModel fallback 路径下为 null;此时退到 `/admin/status.model` 的技术标识
 * (例如 `mock-echo-v1`),给 UI 一个非空字符串展示并塞进 body.model。
 */
type ModelsState =
  | { kind: "loading" }
  | { kind: "ok"; data: ModelsListResponse; activeLabel: string }
  | { kind: "err"; message: string };

type SwitchState =
  | { kind: "idle" }
  | { kind: "switching"; name: string }
  | { kind: "err"; message: string };

export default function Chat() {
  const [modelsState, setModelsState] = useState<ModelsState>({ kind: "loading" });
  const [pendingChoice, setPendingChoice] = useState<string | null>(null);
  const [switchState, setSwitchState] = useState<SwitchState>({ kind: "idle" });

  const [messages, setMessages] = useState<DisplayMsg[]>([]);
  const [input, setInput] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  const loadModels = useCallback(async () => {
    setModelsState({ kind: "loading" });
    try {
      const [status, models] = await Promise.all([api.status(), api.listModels()]);
      const activeLabel = models.active ?? status.model ?? FALLBACK_MODEL_LABEL;
      setModelsState({ kind: "ok", data: models, activeLabel });
      setPendingChoice(models.active);
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setModelsState({ kind: "err", message: msg });
    }
  }, []);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  // auto-scroll to bottom unless user is scrolled up
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
    if (distance < 64) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const activeLabel =
    modelsState.kind === "ok" ? modelsState.activeLabel : FALLBACK_MODEL_LABEL;

  const canSend = !inFlight && input.trim().length > 0;

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || inFlight) return;
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

    try {
      const result = await runTurn(history, {
        model: activeLabel,
        maxTokens: MAX_TOKENS,
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
              model: activeLabel,
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
  }, [input, inFlight, messages, activeLabel]);

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

  const runSwitch = useCallback(async () => {
    if (!pendingChoice || switchState.kind === "switching") return;
    if (modelsState.kind === "ok" && pendingChoice === modelsState.data.active) return;
    setSwitchState({ kind: "switching", name: pendingChoice });
    try {
      await api.useModel(pendingChoice);
      setSwitchState({ kind: "idle" });
      await loadModels();
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setSwitchState({ kind: "err", message: msg });
    }
  }, [pendingChoice, switchState.kind, modelsState, loadModels]);

  return (
    <section className="flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Chat</h1>
        <Button variant="outline" size="sm" onClick={handleNewChat}>
          New chat
        </Button>
      </div>

      <ActiveModelRow
        modelsState={modelsState}
        pendingChoice={pendingChoice}
        onChoose={setPendingChoice}
        switchState={switchState}
        onSwitch={() => void runSwitch()}
      />

      <div
        ref={scrollRef}
        className="mb-3 flex-1 overflow-y-auto rounded-lg border border-border bg-muted/20 p-4"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            输入消息开始对话;流式逐 token 渲染。
          </p>
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

function ActiveModelRow({
  modelsState,
  pendingChoice,
  onChoose,
  switchState,
  onSwitch,
}: {
  modelsState: ModelsState;
  pendingChoice: string | null;
  onChoose: (name: string) => void;
  switchState: SwitchState;
  onSwitch: () => void;
}) {
  if (modelsState.kind === "loading") {
    return (
      <div className="mb-3 text-xs text-muted-foreground">读取 model 列表…</div>
    );
  }

  if (modelsState.kind === "err") {
    return (
      <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        无法读取 model 列表:{modelsState.message}
      </div>
    );
  }

  const { data, activeLabel } = modelsState;
  const isSwitching = switchState.kind === "switching";

  if (data.available.length === 0) {
    return (
      <div className="mb-3 rounded-md border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        当前走 MockModel fallback(<code className="font-mono">{activeLabel}</code>)。
        在 <code className="font-mono">~/.chariot/config.toml</code> 加{" "}
        <code className="rounded bg-muted px-1 py-0.5">[[models]]</code> 后重启 server 即可切换。
      </div>
    );
  }

  return (
    <div className="mb-3 rounded-md border border-border bg-muted/10 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          active model
        </span>
        <Select
          value={pendingChoice ?? undefined}
          onValueChange={onChoose}
          disabled={isSwitching}
        >
          <SelectTrigger className="h-8 w-56">
            <SelectValue placeholder="选 model" />
          </SelectTrigger>
          <SelectContent>
            {data.available.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
                {name === data.active && (
                  <span className="ml-2 text-xs text-muted-foreground">(current)</span>
                )}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          onClick={onSwitch}
          disabled={isSwitching || !pendingChoice || pendingChoice === data.active}
        >
          {isSwitching ? "切换中…" : "切换"}
        </Button>
        <span className="ml-auto text-xs text-muted-foreground">
          切换影响 server 全局 active model,所有会话共享。
        </span>
      </div>
      {switchState.kind === "err" && (
        <p className="mt-1 text-xs text-destructive">切换失败:{switchState.message}</p>
      )}
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
