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
import { api, type ContextInspectResult, type ContextSnapshot, type ContextTrace } from "@/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; id: string }
  | { kind: "ok"; id: string; data: ContextInspectResult }
  | { kind: "err"; id: string; message: string };

export default function Context() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [snapshots, setSnapshots] = useState<ContextSnapshot[]>([]);
  const [traces, setTraces] = useState<ContextTrace[]>([]);
  const [conversationDraft, setConversationDraft] = useState("");
  const [conversationFilter, setConversationFilter] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });

  const load = useCallback(async (conversation_id: string | null) => {
    setState({ kind: "loading" });
    try {
      const [snapRes, traceRes] = await Promise.all([
        api.listContextSnapshots({ conversation_id: conversation_id ?? undefined, limit: 50, offset: 0 }),
        api.listContextTraces({ conversation_id: conversation_id ?? undefined, limit: 50, offset: 0 }),
      ]);
      setSnapshots(snapRes.snapshots);
      setTraces(traceRes.traces);
      setState({ kind: "ok" });
      setDetail({ kind: "idle" });
    } catch (e) {
      setSnapshots([]);
      setTraces([]);
      setDetail({ kind: "idle" });
      setState({ kind: "err", message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  useEffect(() => {
    void load(conversationFilter);
  }, [load, conversationFilter]);

  const summary = useMemo(
    () => ({
      snapshots: snapshots.length,
      traces: traces.length,
    }),
    [snapshots.length, traces.length],
  );

  const applyFilter = useCallback(() => {
    const next = conversationDraft.trim();
    setConversationFilter(next === "" ? null : next);
  }, [conversationDraft]);

  const clearFilter = useCallback(() => {
    setConversationDraft("");
    setConversationFilter(null);
  }, []);

  const refresh = useCallback(() => {
    void load(conversationFilter);
  }, [conversationFilter, load]);

  const inspect = useCallback(
    async (id: string) => {
      if (detail.kind !== "idle" && detail.id === id) {
        setDetail({ kind: "idle" });
        return;
      }
      setDetail({ kind: "loading", id });
      try {
        const data = await api.inspectContext(id);
        setDetail({ kind: "ok", id, data });
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
          <h1 className="text-2xl font-semibold">Context</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Read-only viewer for context snapshots and traces.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={refresh}>
            Refresh
          </Button>
        </div>
      </div>

      <div className="rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1 space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              conversation filter
            </label>
            <Input
              value={conversationDraft}
              onChange={(e) => setConversationDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  applyFilter();
                }
              }}
              placeholder="Optional conversation id"
            />
          </div>
          <Button onClick={applyFilter}>Apply</Button>
          <Button variant="outline" onClick={clearFilter}>
            Clear
          </Button>
          <div className="ml-auto flex flex-wrap gap-2 text-sm text-muted-foreground">
            <Badge variant="outline">{summary.snapshots} snapshots</Badge>
            <Badge variant="outline">{summary.traces} traces</Badge>
          </div>
        </div>
      </div>

      {state.kind === "loading" && <p className="text-sm text-muted-foreground">Loading...</p>}
      {state.kind === "err" && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          Unable to load context data: {state.message}
        </div>
      )}

      {state.kind === "ok" && (
        <div className="space-y-6">
          <section className="space-y-3">
            <SectionHeader
              title="Snapshots"
              subtitle="Turn-level context snapshots. Click inspect to open the raw payload."
              count={snapshots.length}
            />
            <div className="overflow-hidden rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-44">created_at</TableHead>
                    <TableHead className="w-56">conversation</TableHead>
                    <TableHead>provider</TableHead>
                    <TableHead>model</TableHead>
                    <TableHead className="w-24 text-right">size</TableHead>
                    <TableHead className="w-24 text-right">action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {snapshots.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                        No context snapshots yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    snapshots.map((snapshot) => {
                      const active = detail.kind !== "idle" && detail.id === snapshot.id;
                      return (
                        <TableRow key={snapshot.id}>
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            {formatDate(snapshot.created_at)}
                          </TableCell>
                          <TableCell className="font-mono text-xs">
                            {snapshot.conversation_id ?? "-"}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{snapshot.provider_snapshot}</TableCell>
                          <TableCell className="font-mono text-xs">{snapshot.model ?? "-"}</TableCell>
                          <TableCell className="text-right font-mono text-xs">{snapshot.context_size}</TableCell>
                          <TableCell className="text-right">
                            <Button
                              variant={active ? "default" : "outline"}
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => void inspect(snapshot.id)}
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

          <section className="space-y-3">
            <SectionHeader
              title="Traces"
              subtitle="Selection trace for each snapshot. Click inspect to view the linked prompt trace reference."
              count={traces.length}
            />
            <div className="overflow-hidden rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-44">created_at</TableHead>
                    <TableHead className="w-56">snapshot</TableHead>
                    <TableHead>conversation</TableHead>
                    <TableHead>provider</TableHead>
                    <TableHead>model</TableHead>
                    <TableHead className="w-24 text-right">action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {traces.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                        No context traces yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    traces.map((trace) => {
                      const active = detail.kind !== "idle" && detail.id === trace.id;
                      return (
                        <TableRow key={trace.id}>
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            {formatDate(trace.created_at)}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{trace.snapshot_id}</TableCell>
                          <TableCell className="font-mono text-xs">
                            {trace.conversation_id ?? "-"}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{trace.provider_snapshot}</TableCell>
                          <TableCell className="font-mono text-xs">{trace.model ?? "-"}</TableCell>
                          <TableCell className="text-right">
                            <Button
                              variant={active ? "default" : "outline"}
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => void inspect(trace.id)}
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
            <p className="text-sm text-muted-foreground">Loading context detail...</p>
          )}
          {detail.kind === "err" && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              Unable to inspect {detail.id}: {detail.message}
            </div>
          )}
          {detail.kind === "ok" && (
            <section className="rounded-lg border border-border p-4">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="font-mono text-[10px]">
                  context
                </Badge>
                <span className="break-all font-mono text-xs text-muted-foreground">{detail.id}</span>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <DetailBlock
                  title="Snapshot"
                  subtitle="Raw snapshot payload with request, slices, and source refs."
                  value={detail.data.snapshot}
                />
                <DetailBlock
                  title="Trace"
                  subtitle="Raw trace payload with policy and selected refs."
                  value={detail.data.trace}
                />
              </div>
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

function DetailBlock({
  title,
  subtitle,
  value,
}: {
  title: string;
  subtitle: string;
  value: ContextInspectResult["snapshot"] | ContextInspectResult["trace"];
}) {
  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground">{title}</h3>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>
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
