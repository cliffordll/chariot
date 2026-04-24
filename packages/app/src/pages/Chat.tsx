import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  DEFAULT_MODELS,
  MODEL_CHOICES,
  Protocol,
} from "@/lib/api";
import { ChatError, runTurn, type ChatTurnMsg } from "@/lib/chat";

const CUSTOM_MODEL_SENTINEL = "__custom__";

interface MetaInfo {
  model: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  pathLabel: string;
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

export default function Chat() {
  const [protocol, setProtocol] = useState<Protocol>(Protocol.MESSAGES);
  const [model, setModel] = useState<string>(DEFAULT_MODELS[Protocol.MESSAGES]);
  const [useCustomModel, setUseCustomModel] = useState(false);

  const [messages, setMessages] = useState<DisplayMsg[]>([]);
  const [input, setInput] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 切 protocol:把 model 重置为该 protocol 的默认首选,并关掉自定义
  const onProtocolChange = useCallback((next: Protocol) => {
    setProtocol(next);
    setModel(DEFAULT_MODELS[next]);
    setUseCustomModel(false);
  }, []);

  // auto-scroll to bottom unless user is scrolled up
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
    if (distance < 64) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const canSend = !inFlight && input.trim().length > 0 && model.trim().length > 0;

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
        fmt: protocol,
        model,
        maxTokens: 1024,
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
              model,
              inputTokens: result.inputTokens,
              outputTokens: result.outputTokens,
              latencyMs: result.latencyMs,
              pathLabel: protocol,
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
  }, [input, inFlight, messages, protocol, model]);

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
    if (failedIdx < 1) return; // 没找到 user/assistant 对,略过
    const userMsg = copy[failedIdx - 1];
    if (userMsg.role !== "user") return;
    setMessages(copy.slice(0, failedIdx - 1));
    setInput(userMsg.content);
  }, [messages, inFlight]);

  return (
    <section className="flex h-full flex-col">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Chat</h1>
        <Button variant="outline" size="sm" onClick={handleNewChat}>
          New chat
        </Button>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3">
        <div>
          <Label className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
            Protocol
          </Label>
          <Select value={protocol} onValueChange={(v) => onProtocolChange(v as Protocol)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={Protocol.MESSAGES}>messages</SelectItem>
              <SelectItem value={Protocol.CHAT_COMPLETIONS}>completions</SelectItem>
              <SelectItem value={Protocol.RESPONSES}>responses</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div>
          <Label className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
            Model
          </Label>
          {useCustomModel ? (
            <div className="flex gap-1">
              <Input
                value={model}
                placeholder="模型 id"
                onChange={(e) => setModel(e.target.value)}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setUseCustomModel(false);
                  setModel(DEFAULT_MODELS[protocol]);
                }}
              >
                预设
              </Button>
            </div>
          ) : (
            <Select
              value={MODEL_CHOICES[protocol].includes(model) ? model : CUSTOM_MODEL_SENTINEL}
              onValueChange={(v) => {
                if (v === CUSTOM_MODEL_SENTINEL) {
                  setUseCustomModel(true);
                  return;
                }
                setModel(v);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODEL_CHOICES[protocol].map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
                <SelectItem value={CUSTOM_MODEL_SENTINEL}>自定义…</SelectItem>
              </SelectContent>
            </Select>
          )}
        </div>
      </div>

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
    meta.pathLabel,
  ];
  return <div className="text-xs text-muted-foreground font-mono">[{parts.join(" · ")}]</div>;
}

function extractErr(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
