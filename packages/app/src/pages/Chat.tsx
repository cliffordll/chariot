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

/**
 * 高级采样参数(0.2.6)。值随每次请求附在 body 上,server 协议透传给上游。
 * 默认与 Anthropic 默认对齐(temperature=1, top_p=1, max_tokens=1024)。状态在 tab
 * 内持有,刷新会丢失 —— 简化设计,localStorage 持久化等真有人提需求再加。
 */
interface AdvancedParams {
  temperature: number;
  topP: number;
  maxTokens: number;
}

const DEFAULT_PARAMS: AdvancedParams = {
  temperature: 1,
  topP: 1,
  maxTokens: 1024,
};

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

  const [params, setParams] = useState<AdvancedParams>(DEFAULT_PARAMS);
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
        maxTokens: params.maxTokens,
        temperature: params.temperature,
        topP: params.topP,
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
  }, [input, inFlight, messages, activeLabel, params]);

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

      <AdvancedParamsPanel
        params={params}
        onChange={setParams}
        disabled={inFlight}
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
        在 Models 页 <code className="rounded bg-muted px-1 py-0.5">+ Add</code>{" "}
        新建 entry 后,在这里下拉切换。
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

/**
 * 折叠的高级参数面板。折叠时摘要展示当前值;展开后 3 行滑杆 + 数值输入 + Reset。
 *
 * 数值校验:数字输入框接受空串 / 非法值时不更新 state(只读 onBlur 校正)。inFlight
 * 时整体禁用 —— 避免改值 race 已发请求(那条请求保留旧参数,符合直觉)。
 */
function AdvancedParamsPanel({
  params,
  onChange,
  disabled,
}: {
  params: AdvancedParams;
  onChange: (next: AdvancedParams) => void;
  disabled: boolean;
}) {
  const summary = `T=${params.temperature} · top_p=${params.topP} · max=${params.maxTokens}`;
  const isDefault =
    params.temperature === DEFAULT_PARAMS.temperature &&
    params.topP === DEFAULT_PARAMS.topP &&
    params.maxTokens === DEFAULT_PARAMS.maxTokens;

  return (
    <details className="mb-3 rounded-md border border-border bg-muted/10 px-3 py-2 text-xs">
      <summary className="cursor-pointer select-none text-muted-foreground">
        高级参数 <span className="ml-1 font-mono text-foreground/80">{summary}</span>
        {!isDefault && <span className="ml-2 text-amber-600 dark:text-amber-400">(已改)</span>}
      </summary>
      <div className="mt-3 space-y-2">
        <ParamRow
          label="temperature"
          min={0}
          max={1}
          step={0.05}
          value={params.temperature}
          onChange={(v) => onChange({ ...params, temperature: round2(v) })}
          disabled={disabled}
        />
        <ParamRow
          label="top_p"
          min={0}
          max={1}
          step={0.05}
          value={params.topP}
          onChange={(v) => onChange({ ...params, topP: round2(v) })}
          disabled={disabled}
        />
        <ParamRow
          label="max_tokens"
          min={1}
          max={8192}
          step={1}
          value={params.maxTokens}
          onChange={(v) => onChange({ ...params, maxTokens: Math.max(1, Math.round(v)) })}
          disabled={disabled}
          isInt
        />
        <div className="flex items-center justify-between pt-1">
          <span className="text-[11px] text-muted-foreground">
            Anthropic 文档建议 temperature / top_p 只调一项;两者均为 1 时不发到 body。
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onChange(DEFAULT_PARAMS)}
            disabled={disabled || isDefault}
          >
            Reset
          </Button>
        </div>
      </div>
    </details>
  );
}

function ParamRow({
  label,
  min,
  max,
  step,
  value,
  onChange,
  disabled,
  isInt,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  disabled: boolean;
  isInt?: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-24 font-mono text-muted-foreground">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className="flex-1"
      />
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) onChange(isInt ? Math.round(v) : v);
        }}
        disabled={disabled}
        className="w-20 rounded border border-border bg-background px-2 py-1 font-mono text-xs"
      />
    </div>
  );
}

/** 浮点数保留两位,避免滑杆步进累积出 0.30000000000000004 这种值。 */
function round2(v: number): number {
  return Math.round(v * 100) / 100;
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
