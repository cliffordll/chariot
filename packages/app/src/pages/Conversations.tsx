import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  api,
  type Conversation,
  type ConversationSearchHit,
  type Message,
} from "@/lib/api";

type ListState =
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; id: string }
  | {
      kind: "ok";
      id: string;
      conversation: Conversation;
      messages: Message[];
    }
  | { kind: "err"; id: string; message: string };

type SearchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; hits: ConversationSearchHit[]; query: string; scoped: boolean }
  | { kind: "err"; message: string };

export default function Conversations() {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [expandedMessages, setExpandedMessages] = useState<Record<string, boolean>>({});
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState<SearchState>({ kind: "idle" });
  const [rebuilding, setRebuilding] = useState(false);

  const loadList = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const { conversations: list } = await api.listConversations();
      setConversations(list);
      setState({ kind: "ok" });
    } catch (e) {
      setConversations([]);
      setState({ kind: "err", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    setExpandedMessages({});
  }, [detail.kind === "ok" ? detail.id : detail.kind === "loading" || detail.kind === "err" ? detail.id : "idle"]);

  const inspect = useCallback(
    async (id: string) => {
      if (detail.kind !== "idle" && detail.id === id) {
        setDetail({ kind: "idle" });
        return;
      }
      setDetail({ kind: "loading", id });
      try {
        const { conversation, messages } = await api.getConversation(id);
        setDetail({ kind: "ok", id, conversation, messages });
      } catch (e) {
        setDetail({ kind: "err", id, message: e instanceof Error ? e.message : String(e) });
      }
    },
    [detail],
  );

  const runSearch = useCallback(async () => {
    const query = searchDraft.trim();
    if (!query) {
      setSearch({ kind: "idle" });
      return;
    }
    setSearch({ kind: "loading" });
    const scoped = detail.kind === "ok";
    try {
      const { hits } = await api.searchConversation({
        query,
        limit: 30,
        conversation_id: scoped ? detail.id : undefined,
      });
      setSearch({ kind: "ok", hits, query, scoped });
    } catch (e) {
      setSearch({ kind: "err", message: e instanceof Error ? e.message : String(e) });
    }
  }, [searchDraft, detail]);

  const clearSearch = useCallback(() => {
    setSearchDraft("");
    setSearch({ kind: "idle" });
  }, []);

  const rebuildFts = useCallback(async () => {
    setRebuilding(true);
    try {
      const { rebuilt } = await api.rebuildConversationFts();
      window.alert(`Rebuilt FTS index for ${rebuilt} message(s).`);
    } catch (e) {
      window.alert(`Rebuild failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRebuilding(false);
    }
  }, []);

  const summary = useMemo(
    () => ({
      conversations: conversations.length,
      totalMessages: conversations.reduce((acc, c) => acc + c.message_count, 0),
    }),
    [conversations],
  );

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Conversations</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            All chat history. FTS5 full-text search across all messages or scoped to one conversation.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadList}>
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={rebuildFts} disabled={rebuilding}>
            {rebuilding ? "Rebuilding..." : "Rebuild FTS"}
          </Button>
        </div>
      </div>

      <div className="rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-[2] space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              search {detail.kind === "ok" ? `(scoped to ${detail.id.slice(0, 8)}…)` : "(all conversations)"}
            </label>
            <Input
              value={searchDraft}
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void runSearch();
                }
              }}
              placeholder='FTS5 expression, e.g. "fts5 demo" (space = AND)'
            />
          </div>
          <Button onClick={runSearch}>Search</Button>
          <Button variant="outline" onClick={clearSearch}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap gap-2 text-sm text-muted-foreground">
            <Badge variant="outline">{summary.conversations} conversations</Badge>
            <Badge variant="outline">{summary.totalMessages} messages</Badge>
          </div>
        </div>
      </div>

      {search.kind === "loading" && <p className="text-sm text-muted-foreground">Searching…</p>}
      {search.kind === "err" && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          Search failed: {search.message}
        </div>
      )}
      {search.kind === "ok" && (
        <section className="space-y-3">
          <SectionHeader
            title={search.scoped ? "Search hits (scoped)" : "Search hits (global)"}
            subtitle={`Matched "${search.query}" — bm25 rank, click hit to open conversation.`}
            count={search.hits.length}
          />
          {search.hits.length === 0 ? (
            <p className="rounded-md border border-border p-3 text-sm text-muted-foreground">No matches.</p>
          ) : (
            <ul className="space-y-2">
              {search.hits.map((h) => (
                <li
                  key={h.message_id}
                  className="rounded-md border border-border p-3 hover:bg-muted/40 cursor-pointer"
                  onClick={() => void inspect(h.conversation_id)}
                >
                  <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>
                      <span className="font-mono">{h.conversation_id.slice(0, 12)}…</span> · {h.role}
                    </span>
                    <span>rank {h.rank.toFixed(3)}</span>
                  </div>
                  <p
                    className="mt-1 text-sm"
                    // eslint-disable-next-line react/no-danger -- snippet is FTS5 escaped server-side
                    dangerouslySetInnerHTML={{ __html: h.snippet }}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {state.kind === "loading" && <p className="text-sm text-muted-foreground">Loading conversations…</p>}
      {state.kind === "err" && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          Unable to load conversations: {state.message}
        </div>
      )}
      {state.kind === "ok" && (
        <section className="space-y-3">
          <SectionHeader title="Conversations" subtitle="Sorted by updated_at desc." count={conversations.length} />
          <div className="overflow-hidden rounded-lg border border-border">
            <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-44">updated_at</TableHead>
                    <TableHead>title</TableHead>
                    <TableHead className="w-32">last model</TableHead>
                    <TableHead className="w-16 text-right">msgs</TableHead>
                  </TableRow>
                </TableHeader>
              <TableBody>
                {conversations.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="py-10 text-center text-sm text-muted-foreground">
                      No conversations yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  conversations.map((c) => (
                    <Fragment key={c.id}>
                      <TableRow
                        className="cursor-pointer"
                        data-state={detail.kind !== "idle" && detail.id === c.id ? "selected" : undefined}
                        onClick={() => void inspect(c.id)}
                      >
                        <TableCell className="font-mono text-xs">{c.updated_at.replace("T", " ").slice(0, 19)}</TableCell>
                        <TableCell>
                          <div className="font-medium">{c.title ?? "(untitled)"}</div>
                          <div className="text-xs text-muted-foreground font-mono">{c.id}</div>
                        </TableCell>
                        <TableCell>{c.agent_profile ?? "-"}</TableCell>
                        <TableCell className="text-right">{c.message_count}</TableCell>
                      </TableRow>
                      {detail.kind === "loading" && detail.id === c.id && (
                        <TableRow>
                          <TableCell colSpan={4} className="bg-muted/10 px-4 py-4 text-sm text-muted-foreground">
                            Loading conversation detail…
                          </TableCell>
                        </TableRow>
                      )}
                      {detail.kind === "err" && detail.id === c.id && (
                        <TableRow>
                          <TableCell colSpan={4} className="bg-destructive/5 px-4 py-4 text-sm text-destructive">
                            Unable to load conversation: {detail.message}
                          </TableCell>
                        </TableRow>
                      )}
                      {detail.kind === "ok" && detail.id === c.id && (
                        <TableRow>
                          <TableCell colSpan={4} className="bg-muted/10 px-4 py-4">
                            <div className="space-y-3">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline" className="font-mono text-[10px]">
                                  conversation
                                </Badge>
                                <span className="break-all font-mono text-xs text-muted-foreground">{detail.id}</span>
                                <Badge variant="outline">{detail.messages.length}</Badge>
                              </div>
                              <ol className="space-y-2">
                                {detail.messages.map((m, idx) => {
                                  const itemKey = `${detail.id}:${idx}`;
                                  return (
                                    <li key={idx} className="rounded-md border border-border p-3 text-sm">
                                      <div className="text-xs text-muted-foreground">
                                        #{idx + 1} {m.role}
                                        {m.provider_snapshot ? ` [${m.provider_snapshot}]` : ""}
                                      </div>
                                      <ConversationMessageBody
                                        content={stringifyMessageContent(m.content)}
                                        expanded={!!expandedMessages[itemKey]}
                                        onToggle={() =>
                                          setExpandedMessages((cur) => ({
                                            ...cur,
                                            [itemKey]: !cur[itemKey],
                                          }))
                                        }
                                      />
                                    </li>
                                  );
                                })}
                              </ol>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </section>
      )}
    </section>
  );
}

function SectionHeader({ title, subtitle, count }: { title: string; subtitle?: string; count: number }) {
  return (
    <header className="flex items-end justify-between gap-2">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
      </div>
      <Badge variant="outline">{count}</Badge>
    </header>
  );
}

function stringifyMessageContent(content: Message["content"]): string {
  if (typeof content === "string") return content;
  return content
    .map((b) => {
      if (b.type === "text") return b.text;
      if (b.type === "tool_use") return `[tool_use ${b.name} ${JSON.stringify(b.input)}]`;
      if (b.type === "tool_result") return `[tool_result ${typeof b.content === "string" ? b.content : JSON.stringify(b.content)}]`;
      return `[${b.type}]`;
    })
    .join("\n");
}

function ConversationMessageBody({
  content,
  expanded,
  onToggle,
}: {
  content: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const collapsed = content.length > 280;
  const display = !collapsed || expanded ? content : `${content.slice(0, 280)}...`;

  return (
    <div className="mt-1">
      <div className="whitespace-pre-wrap break-words">{display}</div>
      {collapsed && (
        <button
          type="button"
          className="mt-2 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
          onClick={onToggle}
        >
          {expanded ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}
