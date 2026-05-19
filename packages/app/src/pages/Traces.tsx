import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  type ApiError,
  type PromptTrace,
  type TraceExecutionGroup,
  type TraceProviderCall,
  type TraceToolCall,
  type TraceTree,
  type TraceTurn,
  type TurnStatus,
} from "@/lib/api";

type TracesState =
  | { kind: "loading" }
  | { kind: "ok"; turns: TraceTurn[] }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; id: string }
  | { kind: "ok"; tree: TraceTree; promptTrace: PromptTrace | null; promptTraceError: string | null }
  | { kind: "err"; id: string; message: string };

type Filters = {
  conversation: string;
  task: string;
  provider: string;
  status: TurnStatus | "";
};

export default function Traces() {
  const [state, setState] = useState<TracesState>({ kind: "loading" });
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [filters, setFilters] = useState<Filters>({ conversation: "", task: "", provider: "", status: "" });
  const [notice, setNotice] = useState<string | null>(null);

  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const loadTree = useCallback(async (id: string) => {
    setDetail({ kind: "loading", id });
    try {
      const tree = await api.viewTraceTree(id);
      let promptTrace: PromptTrace | null = null;
      let promptTraceError: string | null = null;
      const primaryPromptTraceId = tree.turn.prompt_trace_id;
      if (primaryPromptTraceId) {
        try {
          const result = await api.inspectPrompt(primaryPromptTraceId);
          promptTrace = result.trace;
        } catch (e) {
          promptTraceError = e instanceof Error ? (e as ApiError).message || e.message : String(e);
        }
      }
      setDetail({ kind: "ok", tree, promptTrace, promptTraceError });
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail({ kind: "err", id, message });
    }
  }, []);

  const load = useCallback(
    async (preferredId?: string | null) => {
      setState({ kind: "loading" });
      try {
        const params: Record<string, unknown> = {};
        if (filters.conversation.trim()) params.conversation_id = filters.conversation.trim();
        if (filters.task.trim()) params.task_id = filters.task.trim();
        if (filters.provider.trim()) params.provider_snapshot = filters.provider.trim();
        if (filters.status) params.status = filters.status;
        const { turns } = await api.listTraces(params);
        setState({ kind: "ok", turns });
        const d = detailRef.current;
        const currentId = d.kind === "ok" ? d.tree.turn.id : d.kind === "loading" || d.kind === "err" ? d.id : null;
        const target = preferredId ?? currentId;
        if (target) {
          void loadTree(target);
        } else {
          setDetail({ kind: "idle" });
        }
      } catch (e) {
        const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
        setState({ kind: "err", message });
        setDetail({ kind: "idle" });
      }
    },
    [filters, loadTree],
  );

  const handleSelectTurn = useCallback((id: string) => {
    const current = detailRef.current;
    const currentId =
      current.kind === "ok" ? current.tree.turn.id : current.kind === "loading" || current.kind === "err" ? current.id : null;
    if (currentId === id) {
      setDetail({ kind: "idle" });
      return;
    }
    void loadTree(id);
  }, [loadTree]);

  useEffect(() => {
    void load();
  }, [load]);

  const reconcile = async () => {
    try {
      const { cleaned } = await api.reconcileTraces(3600);
      setNotice(`Reconciled ${cleaned} stale running turn(s).`);
      void load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Traces</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Inspect each AIAgent turn: provider calls, tool calls, and checkpoints. Data layer for reflection / evaluation / RL.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void load()}>Refresh</Button>
          <Button variant="outline" size="sm" onClick={() => void reconcile()}>Reconcile stale</Button>
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-amber-300/30 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {notice}
        </div>
      )}

      <FiltersBar
        filters={filters}
        onChange={setFilters}
        onApply={() => void load()}
        onReset={() =>
          setFilters({ conversation: "", task: "", provider: "", status: "" })
        }
      />

      <TurnsListCard
        state={state}
        detail={detail}
        selectedId={selectedId(detail)}
        onSelect={handleSelectTurn}
      />
    </section>
  );
}

function selectedId(detail: DetailState): string | null {
  if (detail.kind === "ok") return detail.tree.turn.id;
  if (detail.kind === "loading" || detail.kind === "err") return detail.id;
  return null;
}

function FiltersBar({
  filters,
  onChange,
  onApply,
  onReset,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
  onApply: () => void;
  onReset: () => void;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-[repeat(4,minmax(0,1fr))_auto_auto] md:items-end">
      <Field label="Conversation">
        <Input value={filters.conversation} onChange={(e) => onChange({ ...filters, conversation: e.target.value })} placeholder="ULID" />
      </Field>
      <Field label="Task">
        <Input value={filters.task} onChange={(e) => onChange({ ...filters, task: e.target.value })} placeholder="task id" />
      </Field>
      <Field label="Provider">
        <Input value={filters.provider} onChange={(e) => onChange({ ...filters, provider: e.target.value })} placeholder="provider snapshot" />
      </Field>
      <Field label="Status">
        <select
          className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm"
          value={filters.status}
          onChange={(e) => onChange({ ...filters, status: e.target.value as Filters["status"] })}
        >
          <option value="">(all)</option>
          <option value="running">running</option>
          <option value="completed">completed</option>
          <option value="failed">failed</option>
          <option value="cancelled">cancelled</option>
        </select>
      </Field>
      <Button size="sm" onClick={onApply}>Apply</Button>
      <Button size="sm" variant="outline" onClick={onReset}>Clear</Button>
    </div>
  );
}

function TurnsListCard({
  state,
  detail,
  selectedId,
  onSelect,
}: {
  state: TracesState;
  detail: DetailState;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (state.kind === "loading") return <Panel title="Recent Turns">Loading...</Panel>;
  if (state.kind === "err") return <Panel title="Recent Turns" tone="danger">{state.message}</Panel>;

  return (
    <Panel
      title="Recent Turns"
      subtitle={`${state.turns.length} turns · M=模型调用次数 / T=工具调用次数`}
    >
      {state.turns.length === 0 ? (
        <p className="text-sm text-muted-foreground">No trace turns yet. Run a chat to populate.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Status</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>Calls</TableHead>
              <TableHead>Conversation</TableHead>
              <TableHead>Turn</TableHead>
              <TableHead>Started</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.turns.map((t) => (
              <Fragment key={t.id}>
                <TableRow
                  data-state={selectedId === t.id ? "selected" : undefined}
                  className="cursor-pointer"
                  onClick={() => onSelect(t.id)}
                >
                  <TableCell>
                    <StatusBadge status={t.status} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {t.stop_reason ?? "-"}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    M{t.provider_calls_count} / T{t.tool_calls_count}
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {t.conversation_id ?? "-"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {t.id}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDateTime(t.started_at)}</TableCell>
                </TableRow>
                {selectedId === t.id && detail.kind !== "idle" && (
                  <TableRow className="bg-muted/20">
                    <TableCell colSpan={6} className="p-0">
                      <div className="p-4">
                        <TurnDetailInline detail={detail} />
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

function TurnDetailInline({ detail }: { detail: DetailState }) {
  const [sections, setSections] = useState({
    promptTrace: false,
    executionTimeline: false,
    checkpoints: false,
  });
  const detailKey =
    detail.kind === "ok" ? detail.tree.turn.id : detail.kind === "loading" || detail.kind === "err" ? detail.id : "idle";

  useEffect(() => {
    setSections({
      promptTrace: false,
      executionTimeline: false,
      checkpoints: false,
    });
  }, [detailKey]);

  if (detail.kind === "idle") return null;
  if (detail.kind === "loading") return <div className="text-sm text-muted-foreground">Loading {detail.id}...</div>;
  if (detail.kind === "err") {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
        {detail.message}
      </div>
    );
  }

  const { turn, provider_calls, tool_calls, checkpoints, execution_groups } = detail.tree;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>status: {turn.status}</span>
          <span>reason: {turn.stop_reason ?? "-"}</span>
          <span>provider: {turn.provider_snapshot}{turn.model ? `/${turn.model}` : ""}</span>
          {turn.agent_profile && <span>agent: {turn.agent_profile}</span>}
        </div>
        {turn.error_type && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            {turn.error_type}: {turn.error_message || ""}
          </div>
        )}
        <div className="grid gap-1 text-xs text-muted-foreground">
          <span>started: {formatDateTime(turn.started_at)}</span>
          <span>finished: {formatDateTime(turn.finished_at)}</span>
          <span>duration: {formatDuration(turn.duration_ms)}</span>
          <span>
            tokens in/out: {turn.input_tokens ?? "-"}/{turn.output_tokens ?? "-"}
            {turn.cost_usd !== null ? ` · cost: $${turn.cost_usd.toFixed(6)} (${turn.cost_status})` : ""}
          </span>
          {turn.task_id && <span>task: {turn.task_id}</span>}
        </div>
      </div>

      <ReflectionPanel meta={turn.meta} />
      <CollapsibleSection
        title="Turn prompt trace"
        open={sections.promptTrace}
        onToggle={() => setSections((current) => ({ ...current, promptTrace: !current.promptTrace }))}
      >
        <PromptTracePanel
          promptTrace={detail.promptTrace}
          promptTraceError={detail.promptTraceError}
          promptTraceId={turn.prompt_trace_id}
        />
      </CollapsibleSection>
      <CollapsibleSection
        title={`Execution timeline (${provider_calls.length} model / ${tool_calls.length} tool)`}
        open={sections.executionTimeline}
        onToggle={() =>
          setSections((current) => ({
            ...current,
            executionTimeline: !current.executionTimeline,
          }))
        }
      >
        <ExecutionGroupsPanel groups={execution_groups} />
      </CollapsibleSection>
      <CollapsibleSection
        title={`Checkpoints (${checkpoints.length})`}
        open={sections.checkpoints}
        onToggle={() => setSections((current) => ({ ...current, checkpoints: !current.checkpoints }))}
      >
        <SectionList
          items={checkpoints.map((cp) => ({
            key: cp.id,
            line: `${cp.kind} @ ${formatDateTime(cp.created_at)}`,
            details: cp.snapshot_id ? `snapshot=${cp.snapshot_id}` : null,
          }))}
        />
      </CollapsibleSection>
    </div>
  );
}

function CollapsibleSection({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between rounded-md px-1 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
      >
        <span>{title}</span>
        <span aria-hidden="true" className="text-sm leading-none text-foreground/80">{open ? "▴" : "▾"}</span>
      </button>
      {open ? children : null}
    </div>
  );
}

function PromptTracePanel({
  promptTrace,
  promptTraceError,
  promptTraceId,
}: {
  promptTrace: PromptTrace | null;
  promptTraceError: string | null;
  promptTraceId: string | null;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      {promptTrace ? (
        <div className="space-y-3">
          <div className="text-[11px] text-muted-foreground">
            Main prompt snapshot for this turn.
          </div>
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="font-mono text-[11px] break-all text-foreground">{promptTrace.id}</div>
            <div>
              {promptTrace.bundle_name}:{promptTrace.version} / {promptTrace.provider_snapshot}
              {promptTrace.model ? ` / ${promptTrace.model}` : ""} / size {promptTrace.prompt_size}
            </div>
          </div>
          <div className="space-y-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Request</div>
            <pre className="max-h-72 overflow-auto rounded-md border border-border/70 bg-background/80 p-3 text-[11px] leading-5 whitespace-pre-wrap break-all text-muted-foreground">
              {JSON.stringify(promptTrace.request, null, 2)}
            </pre>
          </div>
          <div className="space-y-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Source refs</div>
            <pre className="max-h-72 overflow-auto rounded-md border border-border/70 bg-background/80 p-3 text-[11px] leading-5 whitespace-pre-wrap break-all text-muted-foreground">
              {JSON.stringify(promptTrace.source_refs, null, 2)}
            </pre>
          </div>
        </div>
      ) : promptTraceError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          prompt trace load failed: {promptTraceError}
        </div>
      ) : promptTraceId ? (
        <div className="font-mono text-xs text-muted-foreground">{promptTraceId}</div>
      ) : (
        <div className="text-xs text-muted-foreground">unavailable</div>
      )}
    </div>
  );
}

function ReflectionPanel({ meta }: { meta: Record<string, unknown> }) {
  // B4 wave 2/3:_run_chat_once 写 reflection_iteration / reflection_trigger /
  // reflection_previous_verdict / reflection_previous_reason 进 trace_turns.meta。
  // 首轮 turn 没这些字段;反思触发的第 N 轮 turn 才有。
  const iteration = typeof meta.reflection_iteration === "number" ? meta.reflection_iteration : null;
  if (iteration === null) return null;
  const trigger = typeof meta.reflection_trigger === "string" ? meta.reflection_trigger : "?";
  const verdict = typeof meta.reflection_previous_verdict === "string" ? meta.reflection_previous_verdict : "?";
  const reason = typeof meta.reflection_previous_reason === "string" ? meta.reflection_previous_reason : "";
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-foreground">Reflection</div>
      <div className="space-y-1 text-xs">
        <div>
          <span className="font-mono text-muted-foreground">iteration:</span> {iteration}
        </div>
        <div>
          <span className="font-mono text-muted-foreground">trigger:</span> {trigger}
        </div>
        <div>
          <span className="font-mono text-muted-foreground">previous verdict:</span>{" "}
          <Badge variant={verdict === "FAIL" ? "destructive" : verdict === "PASS" ? "default" : "secondary"}>
            {verdict}
          </Badge>
        </div>
        {reason && (
          <div className="pt-1">
            <span className="font-mono text-muted-foreground">reason:</span>
            <pre className="mt-1 text-xs leading-5 whitespace-pre-wrap break-all">{reason}</pre>
          </div>
        )}
      </div>
    </div>
  );
}

function formatProviderCall(pc: TraceProviderCall): { key: string; line: string; details: string | null } {
  const err = pc.error_type ? `  error=${pc.error_type}` : "";
  const line = `${pc.provider_snapshot}${pc.model ? "/" + pc.model : ""}  latency=${formatDuration(pc.latency_ms)}${err}`;
  const detailsParts: string[] = [];
  if (Object.keys(pc.request_summary).length) {
    detailsParts.push("request: " + JSON.stringify(pc.request_summary));
  }
  if (Object.keys(pc.response_summary).length) {
    detailsParts.push("response: " + JSON.stringify(pc.response_summary));
  }
  return { key: pc.id, line, details: detailsParts.join("\n") || null };
}

function formatToolCall(tc: TraceToolCall): { key: string; line: string; details: string | null } {
  const err = tc.error_message ? `  error=${tc.error_message}` : "";
  const line = `${tc.tool_name}  [${tc.status}]  duration=${formatDuration(tc.duration_ms)}${err}`;
  const detailsParts: string[] = [];
  if (Object.keys(tc.arguments).length) {
    detailsParts.push("args: " + JSON.stringify(tc.arguments));
  }
  if (tc.result_summary) {
    detailsParts.push("result: " + JSON.stringify(tc.result_summary));
  }
  return { key: tc.id, line, details: detailsParts.join("\n") || null };
}

type ExecutionGroup = {
  key: string;
  index: number;
  provider: { key: string; line: string; details: string | null } | null;
  tools: { key: string; line: string; details: string | null }[];
};

function formatExecutionGroups(groups: readonly TraceExecutionGroup[]): ExecutionGroup[] {
  return groups.map((group, idx) => ({
    key: group.provider_call?.id ?? `orphan-tools-${idx}`,
    index: group.index,
    provider: group.provider_call ? formatProviderCall(group.provider_call) : null,
    tools: group.tool_calls.map((tool) => formatToolCall(tool)),
  }));
}

function ExecutionGroupsPanel({
  groups,
}: {
  groups: readonly TraceExecutionGroup[];
}) {
  const formattedGroups = formatExecutionGroups(groups);

  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      {formattedGroups.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">(empty)</p>
      ) : (
        <div className="space-y-3">
          {formattedGroups.map((group) => (
            <div key={group.key} className="rounded-md border border-border/70 bg-background/80 p-3">
              <div className="mb-2 font-mono text-[11px] leading-5 text-foreground">#{group.index}</div>
              {group.provider && (
                <div className="font-mono text-[11px] leading-5 text-foreground">model: {group.provider.line}</div>
              )}
              {group.provider?.details && (
                <pre className="mt-2 border-t border-border/60 pt-2 text-[11px] leading-5 whitespace-pre-wrap break-all text-muted-foreground">
                  {group.provider.details}
                </pre>
              )}
              <div className="mt-3 space-y-2">
                {group.tools.length === 0 ? (
                  <div className="text-[11px] text-muted-foreground">no tool calls</div>
                ) : (
                  group.tools.map((tool) => (
                    <div key={tool.key} className="rounded-md border border-border/60 bg-muted/20 p-3">
                      <div className="font-mono text-[11px] leading-5 text-foreground">tool: {tool.line}</div>
                      {tool.details && (
                        <pre className="mt-2 border-t border-border/60 pt-2 text-[11px] leading-5 whitespace-pre-wrap break-all text-muted-foreground">
                          {tool.details}
                        </pre>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SectionList({
  items,
}: {
  items: { key: string; line: string; details: string | null }[];
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      {items.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">(empty)</p>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.key} className="rounded-md border border-border/70 bg-background/80 p-3 text-[11px]">
              <div className="font-mono leading-5 text-foreground">{item.line}</div>
              {item.details && (
                <pre className="mt-2 border-t border-border/60 pt-2 text-[11px] leading-5 whitespace-pre-wrap break-all text-muted-foreground">
                  {item.details}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: TurnStatus }) {
  const tone =
    status === "completed" ? "default" :
    status === "running" ? "secondary" :
    status === "cancelled" ? "outline" :
    "destructive";
  return <Badge variant={tone as "default" | "secondary" | "outline" | "destructive"}>{status}</Badge>;
}

function Panel({
  title,
  subtitle,
  tone = "default",
  children,
}: {
  title: string;
  subtitle?: string;
  tone?: "default" | "danger";
  children: React.ReactNode;
}) {
  return (
    <div className={`rounded-2xl border p-5 ${tone === "danger" ? "border-destructive/30 bg-destructive/5 text-destructive" : "border-border bg-card/60"}`}>
      <div className="mb-4">
        <div className="text-lg font-semibold">{title}</div>
        {subtitle && <div className="text-sm text-muted-foreground">{subtitle}</div>}
      </div>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function formatDuration(ms: number | null): string {
  if (ms === null) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}
