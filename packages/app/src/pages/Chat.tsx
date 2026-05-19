import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { MarkdownText } from "@/components/MarkdownText";
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
  type AgentProfile,
  type AnthropicBlock,
  type Conversation,
  type Message,
  type ProviderEntry,
  type ProvidersListResponse,
  type ReferenceSuggestion,
} from "@/lib/api";
import { ChatError, runTurn, type ChatTurnMsg } from "@/lib/chat";
import type { StreamEvent } from "@/lib/streams";

const DEFAULT_MAX_TOKENS = 1024;
const DRAFT_KEY = "__draft__";
const CONV_STORAGE_KEY = "chariot.chat.active_conversation";
const DRAFT_AGENT_STORAGE_KEY = "chariot.chat.draft_agent";

interface SamplingValues {
  maxTokens: number;
  temperature: number | undefined;
  topP: number | undefined;
}

interface PendingTurn {
  requestId: number;
  userText: string;
  conversationId: string;
  baseMessageCount: number;
  blocks: AnthropicBlock[];
  status: "streaming" | "done" | "aborted" | "error";
  errorMsg: string | null;
  meta: { inputTokens: number; outputTokens: number; latencyMs: number; model: string } | null;
}

interface SessionState {
  key: string;
  conversationId: string | null;
  conversation: Conversation | null;
  detailStatus: "draft" | "loading" | "loaded" | "error";
  detailError: string | null;
  turnStatus: "idle" | "waiting" | "streaming" | "refreshing" | "done" | "error" | "aborted";
  messages: Message[];
  composerText: string;
  selectedAgent: string | null;
  pending: PendingTurn | null;
  inFlight: boolean;
}

type ProvidersState =
  | { kind: "loading" }
  | { kind: "ok"; data: ProvidersListResponse }
  | { kind: "err"; message: string };

type ConvPaneState =
  | { kind: "draft" }
  | { kind: "loading"; id: string }
  | { kind: "loaded"; id: string; convo: Conversation; messages: Message[] }
  | { kind: "err"; id: string; message: string };

interface ReferencePickerState {
  query: string;
  items: ReferenceSuggestion[];
  index: number;
}

interface ChatPageStoreState {
  activeKey: string;
  sessions: Record<string, SessionState>;
}

function samplingFromEntry(entry: ProviderEntry | undefined): SamplingValues {
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

function upsertConversation(list: Conversation[], convo: Conversation): Conversation[] {
  const rest = list.filter((item) => item.id !== convo.id);
  return [convo, ...rest];
}

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

function extractReferenceQuery(text: string): string | null {
  const token = text.match(/(?:^|\s)(@\S*)$/)?.[1] ?? null;
  return token && token.startsWith("@") ? token : null;
}

function replaceReferenceQuery(text: string, next: string): string {
  return text.replace(/(?:^|\s)(@\S*)$/, (full, token: string) => full.slice(0, full.length - token.length) + next);
}

function makeDraftSession(selectedAgent: string | null): SessionState {
  return {
    key: DRAFT_KEY,
    conversationId: null,
    conversation: null,
    detailStatus: "draft",
    detailError: null,
    turnStatus: "idle",
    messages: [],
    composerText: "",
    selectedAgent,
    pending: null,
    inFlight: false,
  };
}

function createInitialChatPageState(): ChatPageStoreState {
  const initialActiveKey = lsGet(CONV_STORAGE_KEY) ?? DRAFT_KEY;
  const initialDraftAgent = lsGet(DRAFT_AGENT_STORAGE_KEY);
  return {
    activeKey: initialActiveKey,
    sessions: {
      [DRAFT_KEY]: makeDraftSession(initialDraftAgent),
      ...(initialActiveKey !== DRAFT_KEY
        ? {
            [initialActiveKey]: {
              key: initialActiveKey,
              conversationId: initialActiveKey,
              conversation: null,
              detailStatus: "loading",
              detailError: null,
              turnStatus: "idle",
              messages: [],
              composerText: "",
              selectedAgent: null,
              pending: null,
              inFlight: false,
            },
          }
        : {}),
    },
  };
}

let chatPageStoreState: ChatPageStoreState = createInitialChatPageState();
const chatPageStoreListeners = new Set<() => void>();
const chatAbortControllers = new Map<string, AbortController>();

function getChatPageStoreSnapshot(): ChatPageStoreState {
  return chatPageStoreState;
}

function subscribeChatPageStore(listener: () => void): () => void {
  chatPageStoreListeners.add(listener);
  return () => {
    chatPageStoreListeners.delete(listener);
  };
}

function setChatPageStoreState(
  updater: ChatPageStoreState | ((state: ChatPageStoreState) => ChatPageStoreState),
): void {
  const next = typeof updater === "function" ? updater(chatPageStoreState) : updater;
  if (next === chatPageStoreState) return;
  chatPageStoreState = next;
  for (const listener of chatPageStoreListeners) {
    listener();
  }
}

function asBlocks(content: string | AnthropicBlock[]): AnthropicBlock[] {
  if (typeof content === "string") return [{ type: "text", text: content }];
  return content;
}

function blocksPlainText(blocks: AnthropicBlock[]): string | null {
  if (blocks.length === 0 || !blocks.every((b) => b.type === "text")) return null;
  return blocks.map((b) => (b as { text?: string }).text ?? "").join("");
}

function messagePlainText(msg: Message): string | null {
  return blocksPlainText(asBlocks(msg.content));
}

function hasCanonicalTurnEcho(messages: Message[], pending: PendingTurn): boolean {
  if (messages.length <= pending.baseMessageCount) return false;
  const appended = messages.slice(pending.baseMessageCount);
  const hasUserEcho = appended.some(
    (msg) => msg.role === "user" && messagePlainText(msg) === pending.userText,
  );
  if (!hasUserEcho) return false;
  if (pending.blocks.length === 0) {
    return true;
  }
  return appended.some((msg) => msg.role !== "user");
}

function hasCanonicalUserEcho(messages: Message[], pending: PendingTurn): boolean {
  if (messages.length <= pending.baseMessageCount) return false;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role !== "user") continue;
    return messagePlainText(msg) === pending.userText;
  }
  return false;
}

function applyEvent(blocks: AnthropicBlock[], ev: StreamEvent): AnthropicBlock[] {
  if (ev.kind === "text") {
    const last = blocks[blocks.length - 1];
    if (last && last.type === "text" && typeof (last as { text?: unknown }).text === "string") {
      const next = blocks.slice();
      next[next.length - 1] = { type: "text", text: (last as { text: string }).text + ev.text };
      return next;
    }
    return [...blocks, { type: "text", text: ev.text }];
  }
  if (ev.kind === "tool_use") {
    return [
      ...blocks,
      { type: "tool_use", id: ev.toolUseId, name: ev.toolName, input: ev.toolInput },
    ];
  }
  if (ev.kind === "tool_result") {
    return [
      ...blocks,
      {
        type: "tool_result",
        tool_use_id: ev.toolUseId,
        content: ev.toolResultContent,
        is_error: ev.isError,
      },
    ];
  }
  return blocks;
}

function extractErr(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export default function Chat() {
  const [providersState, setProvidersState] = useState<ProvidersState>({ kind: "loading" });
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [convsLoading, setConvsLoading] = useState(true);
  const [convsErr, setConvsErr] = useState<string | null>(null);
  const [referencePicker, setReferencePicker] = useState<ReferencePickerState | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const detailReqSeqRef = useRef<Record<string, number>>({});
  const turnReqSeqRef = useRef(0);
  const { activeKey, sessions } = useSyncExternalStore(subscribeChatPageStore, getChatPageStoreSnapshot);
  const initialActiveKeyRef = useRef(chatPageStoreState.activeKey);

  const activeSession = sessions[activeKey] ?? sessions[DRAFT_KEY];
  const pending = activeSession.pending;
  const isBusy =
    activeSession.turnStatus === "waiting" ||
    activeSession.turnStatus === "streaming" ||
    activeSession.turnStatus === "refreshing";
  const input = activeSession.composerText;
  const selectedAgent = activeSession.selectedAgent;

  const patchSession = useCallback((key: string, updater: (session: SessionState) => SessionState) => {
    setChatPageStoreState((state) => {
      const existing =
        state.sessions[key] ?? (key === DRAFT_KEY ? makeDraftSession(lsGet(DRAFT_AGENT_STORAGE_KEY)) : null);
      if (!existing) return state;
      return {
        ...state,
        sessions: {
          ...state.sessions,
          [key]: updater(existing),
        },
      };
    });
  }, []);

  const loadProviders = useCallback(async () => {
    setProvidersState({ kind: "loading" });
    try {
      const { providers } = await api.listProviders();
      setProvidersState({
        kind: "ok",
        data: {
          available: providers.map((p) => p.name),
          types: [],
          entries: providers,
        },
      });
    } catch (e) {
      setProvidersState({ kind: "err", message: extractErr(e) });
    }
  }, []);

  const loadConvs = useCallback(async (opts?: { preserveList?: boolean }) => {
    if (!opts?.preserveList) {
      setConvsLoading(true);
    }
    setConvsErr(null);
    try {
      const { conversations } = await api.listConversations();
      setConvs(conversations.slice(0, 100));
    } catch (e) {
      setConvsErr(extractErr(e));
    } finally {
      setConvsLoading(false);
    }
  }, []);

  const loadConversation = useCallback(async (id: string, opts?: { activate?: boolean; preserveMessages?: boolean }) => {
    const activate = opts?.activate !== false;
    const preserveMessages = opts?.preserveMessages === true;
    const requestSeq = (detailReqSeqRef.current[id] ?? 0) + 1;
    detailReqSeqRef.current[id] = requestSeq;
    if (activate) {
      setChatPageStoreState((state) => ({ ...state, activeKey: id }));
    }
    setChatPageStoreState((state) => {
      const existing = state.sessions[id];
      const next: SessionState = existing ?? {
        key: id,
        conversationId: id,
        conversation: null,
        detailStatus: "loading",
        detailError: null,
        turnStatus: "idle",
        messages: [],
        composerText: "",
        selectedAgent: null,
        pending: null,
        inFlight: false,
      };
      return {
        ...state,
        sessions: {
          ...state.sessions,
          [id]: {
            ...next,
            detailStatus: preserveMessages && next.messages.length > 0 ? "loaded" : "loading",
            detailError: null,
          },
        },
      };
    });
    try {
      const detail = await api.getConversation(id);
      if (detailReqSeqRef.current[id] !== requestSeq) return;
      setChatPageStoreState((state) => {
        const existing = state.sessions[id];
        if (!existing) return state;
        const shouldClearPending =
          existing.pending !== null && hasCanonicalTurnEcho(detail.messages, existing.pending);
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [id]: {
              ...existing,
              conversationId: id,
              conversation: detail.conversation,
              detailStatus: "loaded",
              detailError: null,
              messages: detail.messages,
              selectedAgent: detail.conversation.agent_profile,
              pending: shouldClearPending ? null : existing.pending,
              inFlight: shouldClearPending ? false : existing.inFlight,
              turnStatus: shouldClearPending ? "done" : existing.turnStatus,
            },
          },
        };
      });
    } catch (e) {
      if (detailReqSeqRef.current[id] !== requestSeq) return;
      setChatPageStoreState((state) => {
        const existing = state.sessions[id];
        if (!existing) return state;
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [id]: {
              ...existing,
              detailStatus: "error",
              detailError: extractErr(e),
            },
          },
        };
      });
    }
  }, []);

  useEffect(() => {
    lsSet(CONV_STORAGE_KEY, activeKey === DRAFT_KEY ? null : activeKey);
  }, [activeKey]);

  useEffect(() => {
    lsSet(DRAFT_AGENT_STORAGE_KEY, sessions[DRAFT_KEY]?.selectedAgent ?? null);
  }, [sessions]);

  useEffect(() => {
    void loadProviders();
    void loadConvs();
    void (async () => {
      try {
        const { agents: list } = await api.listAgents();
        setAgents(list);
      } catch {
        // ignore
      }
    })();
  }, [loadProviders, loadConvs]);

  useEffect(() => {
    if (initialActiveKeyRef.current !== DRAFT_KEY) {
      void loadConversation(initialActiveKeyRef.current, { activate: false });
    }
  }, [loadConversation]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [activeKey, activeSession.messages, activeSession.pending]);

  useEffect(() => {
    const query = extractReferenceQuery(input);
    if (!query) {
      setReferencePicker(null);
      return;
    }
    if (query.startsWith("@session:")) {
      const items = convs
        .slice(0, 8)
        .map((c) => ({
          value: `@session:${c.id}`,
          label: `${c.title?.trim() || "(untitled)"} · ${c.id}`,
          kind: "session",
        }))
        .filter((item) => item.value.startsWith(query));
      setReferencePicker(items.length ? { query, items, index: 0 } : null);
      return;
    }
    if (query.startsWith("@file:") || ["@file:", "@url:", "@diff:", "@session:"].some((prefix) => prefix.startsWith(query))) {
      let cancelled = false;
      void api.completeReference(query).then(({ items }) => {
        if (!cancelled) {
          setReferencePicker(items.length ? { query, items, index: 0 } : null);
        }
      }).catch(() => {
        if (!cancelled) setReferencePicker(null);
      });
      return () => {
        cancelled = true;
      };
    }
    setReferencePicker(null);
  }, [input, convs]);

  const updateComposer = useCallback((key: string, value: string) => {
    patchSession(key, (session) => ({ ...session, composerText: value }));
  }, [patchSession]);

  const startNewChat = useCallback(() => {
    chatAbortControllers.get(DRAFT_KEY)?.abort();
    chatAbortControllers.delete(DRAFT_KEY);
    setChatPageStoreState((state) => ({
      ...state,
      sessions: {
        ...state.sessions,
        [DRAFT_KEY]: {
          ...makeDraftSession(state.sessions[DRAFT_KEY]?.selectedAgent ?? lsGet(DRAFT_AGENT_STORAGE_KEY)),
        },
      },
    }));
    setChatPageStoreState((state) => ({ ...state, activeKey: DRAFT_KEY }));
  }, []);

  const selectConv = useCallback((id: string) => {
    if (activeKey === id) return;
    const existing = sessions[id];
    setChatPageStoreState((state) => ({ ...state, activeKey: id }));
    if (!existing || existing.detailStatus === "error" || existing.messages.length === 0) {
      void loadConversation(id, { activate: false });
    }
  }, [activeKey, loadConversation, sessions]);

  const handleDeleteConv = useCallback(async (id: string) => {
    if (!window.confirm("删除这个对话?不可撤销。")) return;
    try {
      await api.deleteConversation(id);
    } catch (e) {
      alert(`删除失败: ${extractErr(e)}`);
      return;
    }
    chatAbortControllers.get(id)?.abort();
    chatAbortControllers.delete(id);
    setChatPageStoreState((state) => {
      const nextSessions = { ...state.sessions };
      delete nextSessions[id];
      return {
        ...state,
        sessions: nextSessions,
      };
    });
    if (activeKey === id) {
      setChatPageStoreState((state) => ({ ...state, activeKey: DRAFT_KEY }));
    }
    void loadConvs({ preserveList: true });
  }, [activeKey, loadConvs]);

  const handleRenameConv = useCallback(async (id: string, currentTitle: string | null) => {
    const next = window.prompt("新标题(空字符串清空):", currentTitle ?? "");
    if (next === null) return;
    const title = next.trim() === "" ? null : next.trim();
    try {
      const { conversation } = await api.renameConversation(id, title);
      setConvs((cur) => upsertConversation(cur, conversation));
      patchSession(id, (session) => ({
        ...session,
        conversation,
      }));
    } catch (e) {
      alert(`改名失败: ${extractErr(e)}`);
      return;
    }
    void loadConvs({ preserveList: true });
    void loadConversation(id, { activate: false, preserveMessages: true });
  }, [loadConversation, loadConvs, patchSession]);

  const handleSelectAgent = useCallback((name: string | null) => {
    const key = activeKey;
    patchSession(key, (session) => ({ ...session, selectedAgent: name }));
    if (key === DRAFT_KEY) return;
    api.updateConversationConfig(key, { agent_profile: name }).then(({ conversation }) => {
      setConvs((cur) => upsertConversation(cur, conversation));
      patchSession(key, (session) => ({ ...session, conversation }));
    }).catch(() => {});
  }, [activeKey, patchSession]);

  const refreshAfterTurn = useCallback(async (key: string, conversationId: string, requestId: number) => {
    await loadConversation(conversationId, { activate: false, preserveMessages: true });
    await loadConvs({ preserveList: true });
    setChatPageStoreState((state) => {
      const session = state.sessions[key];
      if (!session?.pending || session.pending.requestId !== requestId || session.pending.status !== "done") {
        return state;
      }
      return {
        ...state,
        sessions: {
          ...state.sessions,
          [key]: {
            ...session,
            inFlight: false,
            pending: null,
            turnStatus: "done",
          },
        },
      };
    });
  }, [loadConversation, loadConvs]);

  const handleSend = useCallback(async () => {
    const session = sessions[activeKey];
    if (!session) return;
    const text = session.composerText.trim();
    if (
      !text ||
      session.turnStatus === "waiting" ||
      session.turnStatus === "streaming" ||
      session.turnStatus === "refreshing" ||
      session.selectedAgent === null
    ) return;

    const requestId = ++turnReqSeqRef.current;
    const pendingTurn: PendingTurn = {
      requestId,
      userText: text,
      conversationId: session.conversationId ?? DRAFT_KEY,
      baseMessageCount: session.messages.length,
      blocks: [],
      status: "streaming",
      errorMsg: null,
      meta: null,
    };

    patchSession(activeKey, (current) => ({
      ...current,
      composerText: "",
      pending: pendingTurn,
      turnStatus: "waiting",
      inFlight: true,
    }));

    let targetKey = activeKey;
    let conversationId = session.conversationId;
    let agentForTurn = session.selectedAgent;

    if (activeKey === DRAFT_KEY) {
      try {
        const created = await api.createConversation({});
        const conversation: Conversation = { ...created, agent_profile: agentForTurn };
        setConvs((cur) => upsertConversation(cur, conversation));
        targetKey = conversation.id;
        conversationId = conversation.id;
        setChatPageStoreState((state) => ({
          ...state,
          sessions: {
            ...state.sessions,
            [DRAFT_KEY]: makeDraftSession(state.sessions[DRAFT_KEY]?.selectedAgent ?? lsGet(DRAFT_AGENT_STORAGE_KEY)),
            [conversation.id]: {
              key: conversation.id,
              conversationId: conversation.id,
              conversation,
              detailStatus: "loaded",
              detailError: null,
              turnStatus: "waiting",
              messages: [],
              composerText: "",
              selectedAgent: agentForTurn,
              pending: { ...pendingTurn, conversationId: conversation.id, baseMessageCount: 0 },
              inFlight: true,
            },
          },
        }));
        setChatPageStoreState((state) => ({ ...state, activeKey: conversation.id }));
        api.updateConversationConfig(conversation.id, { agent_profile: agentForTurn }).catch(() => {});
      } catch (e) {
        patchSession(DRAFT_KEY, (current) => ({
          ...current,
          pending: {
            ...pendingTurn,
            status: "error",
            errorMsg: `创建会话失败: ${extractErr(e)}`,
          },
          turnStatus: "error",
          inFlight: false,
        }));
        return;
      }
    }

    if (!conversationId || agentForTurn === null) return;

    const agentObj = agents.find((a) => a.id === agentForTurn);
    const entryId = agentObj?.provider_id ?? null;
    const entry =
      providersState.kind === "ok"
        ? providersState.data.entries.find((e) => e.id === entryId)
        : undefined;
    const sampling = samplingFromEntry(entry);
    const ctrl = new AbortController();
    chatAbortControllers.set(targetKey, ctrl);

    try {
      const result = await runTurn([{ role: "user", content: text } satisfies ChatTurnMsg], {
        provider: null,
        maxTokens: sampling.maxTokens,
        temperature: sampling.temperature,
        topP: sampling.topP,
        conversationId,
        agentProfile: agentForTurn,
        signal: ctrl.signal,
        onEvent: (ev) => {
          setChatPageStoreState((state) => {
            const current = state.sessions[targetKey];
            if (!current?.pending || current.pending.requestId !== requestId) return state;
            return {
              ...state,
              sessions: {
                ...state.sessions,
                [targetKey]: {
                  ...current,
                  turnStatus: "streaming",
                  pending: {
                    ...current.pending,
                    blocks: applyEvent(current.pending.blocks, ev),
                  },
                },
              },
            };
          });
        },
      });
      setChatPageStoreState((state) => {
        const current = state.sessions[targetKey];
        if (!current?.pending || current.pending.requestId !== requestId) return state;
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [targetKey]: {
              ...current,
              inFlight: false,
              turnStatus: result.aborted ? "aborted" : "refreshing",
              pending: {
                ...current.pending,
                status: result.aborted ? "aborted" : "done",
                meta: {
                  inputTokens: result.inputTokens,
                  outputTokens: result.outputTokens,
                  latencyMs: result.latencyMs,
                  model: entryId ?? "?",
                },
              },
            },
          },
        };
      });
      if (!result.aborted) {
        void refreshAfterTurn(targetKey, conversationId, requestId);
      }
    } catch (e) {
      setChatPageStoreState((state) => {
        const current = state.sessions[targetKey];
        if (!current?.pending || current.pending.requestId !== requestId) return state;
        return {
          ...state,
          sessions: {
            ...state.sessions,
            [targetKey]: {
              ...current,
              inFlight: false,
              turnStatus: "error",
              pending: {
                ...current.pending,
                status: "error",
                errorMsg: e instanceof ChatError ? e.message : extractErr(e),
              },
            },
          },
        };
      });
    } finally {
      chatAbortControllers.delete(targetKey);
    }
  }, [activeKey, agents, patchSession, providersState, refreshAfterTurn, sessions]);

  const handleStop = useCallback(() => {
    chatAbortControllers.get(activeKey)?.abort();
  }, [activeKey]);

  const handleRetry = useCallback(() => {
    const session = sessions[activeKey];
    if (
      !session?.pending ||
      session.turnStatus === "waiting" ||
      session.turnStatus === "streaming" ||
      session.turnStatus === "refreshing"
    ) return;
    if (session.pending.status !== "error" && session.pending.status !== "aborted") return;
    patchSession(activeKey, (current) => ({
      ...current,
      composerText: current.pending?.userText ?? current.composerText,
      turnStatus: "idle",
      pending: null,
    }));
  }, [activeKey, patchSession, sessions]);

  const applyReferenceSuggestion = useCallback((item: ReferenceSuggestion) => {
    updateComposer(activeKey, `${replaceReferenceQuery(input, item.value)} `);
    setReferencePicker(null);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      const len = textareaRef.current?.value.length ?? 0;
      textareaRef.current?.setSelectionRange(len, len);
    });
  }, [activeKey, input, updateComposer]);

  const canSend =
    !isBusy &&
    input.trim().length > 0 &&
    selectedAgent !== null &&
    (activeSession.detailStatus === "draft" || activeSession.detailStatus === "loaded");

  const activePane: ConvPaneState = useMemo(() => {
    if (activeKey === DRAFT_KEY) return { kind: "draft" };
    if (activeSession.detailStatus === "loading") return { kind: "loading", id: activeKey };
    if (activeSession.detailStatus === "error") {
      return { kind: "err", id: activeKey, message: activeSession.detailError ?? "load failed" };
    }
    return {
      kind: "loaded",
      id: activeKey,
      convo: activeSession.conversation ?? {
        id: activeKey,
        title: null,
        agent_profile: activeSession.selectedAgent,
        last_model: null,
        created_at: "",
        updated_at: "",
        message_count: activeSession.messages.length,
      },
      messages: activeSession.messages,
    };
  }, [activeKey, activeSession]);

  return (
    <section className="flex h-full gap-3">
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
            <li
              className={
                "group flex cursor-pointer items-start gap-1 px-2 py-2 text-xs hover:bg-muted/30 " +
                (activeKey === DRAFT_KEY ? "bg-muted/40" : "")
              }
              onClick={startNewChat}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">New chat</div>
                <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                  draft
                </div>
              </div>
            </li>
            {convs.map((c) => {
              const session = sessions[c.id];
              const isStreaming =
                session?.turnStatus === "waiting" ||
                session?.turnStatus === "streaming" ||
                session?.turnStatus === "refreshing";
              const statusLabel =
                session?.turnStatus === "waiting"
                  ? "waiting"
                  : session?.turnStatus === "streaming"
                    ? "streaming"
                  : session?.turnStatus === "refreshing"
                    ? "syncing"
                    : session?.turnStatus === "done"
                      ? "done"
                      : session?.turnStatus === "error"
                        ? "error"
                        : session?.turnStatus === "aborted"
                          ? "aborted"
                          : null;
              return (
                <li
                  key={c.id}
                  className={
                    "group flex cursor-pointer items-start gap-1 px-2 py-2 text-xs hover:bg-muted/30 " +
                    (activeKey === c.id ? "bg-muted/40" : "")
                  }
                  onClick={() => selectConv(c.id)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1 truncate font-medium">
                      {isStreaming && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />}
                      {c.title?.trim() ? c.title : <span className="text-muted-foreground">(no title)</span>}
                    </div>
                    <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                      <code className="font-mono">{c.id.slice(-6)}</code>
                      {" · "}
                      {c.message_count} msgs
                      {statusLabel && (
                        <>
                          {" · "}
                          {statusLabel}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-center opacity-0 transition group-hover:opacity-100">
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

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="mb-3 flex items-center justify-between">
          <h1 className="text-2xl font-semibold">
            {activePane.kind === "loaded" && activePane.convo.title
              ? activePane.convo.title
              : activePane.kind === "draft"
                ? "New chat"
                : "Chat"}
          </h1>
          {activePane.kind === "loaded" && (
            <div className="text-xs text-muted-foreground">
              <code className="font-mono">{activePane.id}</code>
            </div>
          )}
        </div>

        <EntryRow
          providersState={providersState}
          agents={agents}
          selectedAgent={selectedAgent}
          onSelectAgent={handleSelectAgent}
        />

        <div
          ref={scrollRef}
          className="mb-3 flex-1 overflow-y-auto rounded-lg border border-border bg-muted/20 p-4"
        >
          <MessagesView pane={activePane} pending={pending} onRetry={handleRetry} />
        </div>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Textarea
              ref={textareaRef}
              value={input}
              placeholder={
                selectedAgent === null
                  ? "请先选择 agent_profile，再开始对话"
                  : "发消息…(Enter 发送,Shift+Enter 换行)"
              }
              onChange={(e) => updateComposer(activeKey, e.target.value)}
              onKeyDown={(e) => {
                if (referencePicker && referencePicker.items.length > 0) {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setReferencePicker((cur) =>
                      cur ? { ...cur, index: (cur.index + 1) % cur.items.length } : cur,
                    );
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setReferencePicker((cur) =>
                      cur ? { ...cur, index: (cur.index - 1 + cur.items.length) % cur.items.length } : cur,
                    );
                    return;
                  }
                  if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                    e.preventDefault();
                    void applyReferenceSuggestion(referencePicker.items[referencePicker.index]);
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setReferencePicker(null);
                    return;
                  }
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (canSend) void handleSend();
                }
              }}
              disabled={isBusy}
              className="min-h-20 flex-1"
            />
            {referencePicker && (
              <div className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-md border border-border bg-background shadow-lg">
                <div className="border-b border-border px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground">
                  reference suggestions
                </div>
                <ul className="max-h-56 overflow-y-auto py-1">
                  {referencePicker.items.map((item, index) => (
                    <li key={`${item.kind}:${item.value}`}>
                      <button
                        type="button"
                        className={
                          "flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-muted/50 " +
                          (index === referencePicker.index ? "bg-muted/60" : "")
                        }
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => applyReferenceSuggestion(item)}
                      >
                        <span className="truncate">{item.label}</span>
                        <span className="ml-3 shrink-0 font-mono text-[11px] text-muted-foreground">
                          {item.kind}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {selectedAgent === null && (
              <p className="mt-2 text-xs text-muted-foreground">请先选择 `agent_profile`。</p>
            )}
            {selectedAgent !== null && (
              <p className="mt-2 text-xs text-muted-foreground">
                输入 `@` 可补全 `@file:`、`@session:`、`@url:`、`@diff:`。冒号后可带空格，发送前会自动按引用格式解析。
              </p>
            )}
          </div>
          {isBusy ? (
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

const AGENT_NONE = "__none__";

function EntryRow({
  providersState,
  agents,
  selectedAgent,
  onSelectAgent,
}: {
  providersState: ProvidersState;
  agents: AgentProfile[];
  selectedAgent: string | null;
  onSelectAgent: (name: string | null) => void;
}) {
  if (providersState.kind === "loading") {
    return <div className="mb-3 text-xs text-muted-foreground">读取 entries…</div>;
  }
  if (providersState.kind === "err") {
    return (
      <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        无法读取 entries:{providersState.message}
      </div>
    );
  }

  const agent = agents.find((a) => a.id === selectedAgent);
  const providerName = agent?.provider_label ?? agent?.provider_id ?? null;
  const promptName = agent?.prompt_label ?? agent?.prompt_id ?? null;
  const toolsetName = agent?.toolset_label ?? agent?.toolset_id ?? null;

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/10 p-3">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">
        agent
      </span>
      <Select
        value={selectedAgent ?? AGENT_NONE}
        onValueChange={(v) => onSelectAgent(v === AGENT_NONE ? null : v)}
      >
        <SelectTrigger className="h-8 w-48">
          <SelectValue placeholder="(none)" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AGENT_NONE}>(none)</SelectItem>
          {agents.map((a) => (
            <SelectItem key={a.id} value={a.id}>
              {a.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
        {`provider: ${providerName ?? "(none)"} | prompt: ${promptName ?? "(none)"} | toolset: ${toolsetName ?? "(none)"}`}
      </span>
    </div>
  );
}

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
    return pending ? (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">Loading conversation…</p>
        <PendingBubble pending={pending} onRetry={onRetry} />
      </div>
    ) : (
      <p className="text-sm text-muted-foreground">Loading conversation…</p>
    );
  }
  if (pane.kind === "err") {
    return pending ? (
      <div className="space-y-3">
        <p className="text-sm text-destructive">无法读取对话:{pane.message}</p>
        <PendingBubble pending={pending} onRetry={onRetry} />
      </div>
    ) : (
      <p className="text-sm text-destructive">无法读取对话:{pane.message}</p>
    );
  }
  const hidePendingUserBubble = pending ? hasCanonicalUserEcho(pane.messages, pending) : false;
  if (pane.messages.length === 0 && !pending) {
    return (
      <p className="text-sm text-muted-foreground">
        空对话:输入消息开始。
      </p>
    );
  }
  return (
    <ul className="space-y-4">
      {pane.messages.map((m, i) => (
        <li key={m.seq ?? i}>
          <MessageRow msg={m} />
        </li>
      ))}
      {pending && (
        <li>
          <PendingBubble
            pending={pending}
            onRetry={onRetry}
            hideUserBubble={hidePendingUserBubble}
          />
        </li>
      )}
    </ul>
  );
}

function MessageRow({ msg }: { msg: Message }) {
  if (msg.role === "user") {
    const blocks = asBlocks(msg.content);
    const text = blocksPlainText(blocks);
    if (text !== null) {
      return (
        <div className="flex justify-end">
          <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
            {text}
          </div>
        </div>
      );
    }
    return (
      <div className="flex flex-col items-start gap-1">
        <BlocksRender blocks={blocks} side="tool" />
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1">
      {typeof msg.content === "string" ? (
        <AssistantTextBubble text={msg.content} />
      ) : (
        <BlocksRender blocks={asBlocks(msg.content)} side="assistant" />
      )}
      {msg.provider_snapshot && (
        <div className="font-mono text-xs text-muted-foreground">
          [{msg.provider_snapshot}]
        </div>
      )}
    </div>
  );
}

function AssistantTextBubble({ text }: { text: string }) {
  return (
    <div className="max-w-[85%] rounded-lg border border-border bg-background px-3 py-2 text-sm">
      {text ? (
        <MarkdownText text={text} />
      ) : (
        <span className="text-muted-foreground">(empty)</span>
      )}
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
    return <AssistantTextBubble text={(block as { text: string }).text} />;
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
  hideUserBubble = false,
}: {
  pending: PendingTurn;
  onRetry: () => void;
  hideUserBubble?: boolean;
}) {
  return (
    <div className="space-y-3">
      {!hideUserBubble && (
        <div className="flex justify-end">
          <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
            {pending.userText}
          </div>
        </div>
      )}
      <div className="flex flex-col items-start gap-1">
        {pending.blocks.length === 0 && pending.status === "streaming" && (
          <div className="flex max-w-[85%] items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            <span className="text-muted-foreground">Thinking...</span>
          </div>
        )}
        {pending.blocks.length > 0 && (
          <BlocksRender blocks={pending.blocks} side="assistant" />
        )}
        {pending.status === "aborted" && (
          <span className="text-xs text-muted-foreground">[已中断]</span>
        )}
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
