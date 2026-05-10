import { useCallback, useEffect, useMemo, useState } from "react";

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
import { api, type MemoryEntry, type MemoryEvent, type MemoryLink } from "@/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; id: string }
  | { kind: "ok"; id: string; memory: MemoryEntry; events: MemoryEvent[]; links: MemoryLink[] }
  | { kind: "err"; id: string; message: string };

export default function Memory() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [kindDraft, setKindDraft] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [kindFilter, setKindFilter] = useState<string | null>(null);
  const [searchFilter, setSearchFilter] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });

  const load = useCallback(async (params: { kind: string | null; search: string | null }) => {
    setState({ kind: "loading" });
    try {
      const res = await api.listMemories({
        kind: params.kind ?? undefined,
        search: params.search ?? undefined,
        limit: 80,
      });
      setMemories(res.entries);
      setState({ kind: "ok" });
      setDetail({ kind: "idle" });
    } catch (e) {
      setMemories([]);
      setDetail({ kind: "idle" });
      setState({ kind: "err", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  useEffect(() => {
    void load({ kind: kindFilter, search: searchFilter });
  }, [kindFilter, load, searchFilter]);

  const summary = useMemo(
    () => ({
      memories: memories.length,
    }),
    [memories.length],
  );

  const applyFilter = useCallback(() => {
    setKindFilter(kindDraft.trim() === "" ? null : kindDraft.trim());
    setSearchFilter(searchDraft.trim() === "" ? null : searchDraft.trim());
  }, [kindDraft, searchDraft]);

  const clearFilter = useCallback(() => {
    setKindDraft("");
    setSearchDraft("");
    setKindFilter(null);
    setSearchFilter(null);
  }, []);

  const refresh = useCallback(() => {
    void load({ kind: kindFilter, search: searchFilter });
  }, [kindFilter, load, searchFilter]);

  const inspect = useCallback(
    async (id: string) => {
      if (detail.kind !== "idle" && detail.id === id) {
        setDetail({ kind: "idle" });
        return;
      }
      setDetail({ kind: "loading", id });
      try {
        const [memoryRes, eventRes, linkRes] = await Promise.all([
          api.getMemory(id),
          api.listMemoryEvents(id),
          api.listMemoryLinks(id),
        ]);
        setDetail({
          kind: "ok",
          id,
          memory: memoryRes.memory,
          events: eventRes.events,
          links: linkRes.links,
        });
      } catch (e) {
        setDetail({
          kind: "err",
          id,
          message: e instanceof Error ? e.message : String(e),
        });
      }
    },
    [detail],
  );

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Memory</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Read-only viewer for long-term memories, events, and links.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          Refresh
        </Button>
      </div>

      <div className="rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-44 flex-1 space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">kind</label>
            <Input
              value={kindDraft}
              onChange={(e) => setKindDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  applyFilter();
                }
              }}
              placeholder="Optional memory kind"
            />
          </div>
          <div className="min-w-64 flex-[2] space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">search</label>
            <Input
              value={searchDraft}
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  applyFilter();
                }
              }}
              placeholder="Search memory text"
            />
          </div>
          <Button onClick={applyFilter}>Apply</Button>
          <Button variant="outline" onClick={clearFilter}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap gap-2 text-sm text-muted-foreground">
            <Badge variant="outline">{summary.memories} memories</Badge>
          </div>
        </div>
      </div>

      {state.kind === "loading" && <p className="text-sm text-muted-foreground">Loading...</p>}
      {state.kind === "err" && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          Unable to load memory data: {state.message}
        </div>
      )}

      {state.kind === "ok" && (
        <div className="space-y-6">
          <section className="space-y-3">
            <SectionHeader
              title="Memories"
              subtitle="Entries are read-only here; use `chariot memory` for management."
              count={memories.length}
            />
            <div className="overflow-hidden rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-44">created_at</TableHead>
                    <TableHead className="w-32">kind</TableHead>
                    <TableHead className="w-20 text-center">pinned</TableHead>
                    <TableHead className="w-24 text-center">archived</TableHead>
                    <TableHead>text</TableHead>
                    <TableHead className="w-24 text-right">action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {memories.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                        No memory entries yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    memories.map((memory) => {
                      const active = detail.kind !== "idle" && detail.id === memory.id;
                      return (
                        <TableRow key={memory.id}>
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            {formatDate(memory.created_at)}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{memory.kind}</TableCell>
                          <TableCell className="text-center font-mono text-xs">
                            {memory.pinned ? "yes" : "no"}
                          </TableCell>
                          <TableCell className="text-center font-mono text-xs">
                            {memory.archived ? "yes" : "no"}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{truncate(memory.text, 90)}</TableCell>
                          <TableCell className="text-right">
                            <Button
                              variant={active ? "default" : "outline"}
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => void inspect(memory.id)}
                            >
                              {active ? "Hide" : "Inspect"}
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </section>

          {detail.kind === "loading" && (
            <p className="text-sm text-muted-foreground">Loading memory detail...</p>
          )}
          {detail.kind === "err" && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              Unable to inspect {detail.id}: {detail.message}
            </div>
          )}
          {detail.kind === "ok" && (
            <section className="space-y-4 rounded-lg border border-border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="font-mono text-[10px]">
                  memory
                </Badge>
                <span className="break-all font-mono text-xs text-muted-foreground">{detail.id}</span>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <JsonBlock title="Memory" value={detail.memory} />
                <JsonBlock title="Meta" value={detail.memory.meta} />
              </div>
              <JsonBlock title="Events" value={detail.events} />
              <JsonBlock title="Links" value={detail.links} />
            </section>
          )}
        </div>
      )}
    </section>
  );
}

function SectionHeader({
  title,
  subtitle,
  count,
}: {
  title: string;
  subtitle: string;
  count: number;
}) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>
      <Badge variant="outline">{count}</Badge>
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground">{title}</h3>
      <pre className="max-h-[28rem] overflow-auto rounded-md border border-border bg-muted/20 p-4 text-xs leading-6 whitespace-pre-wrap break-words">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function formatDate(value: string): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function truncate(text: string, limit: number): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1)}…`;
}
