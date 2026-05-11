import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  type AuditEvent,
  type CapabilityEntry,
  type CheckpointEntry,
  type GuardrailRule,
  type GuardrailVerdict,
  type RollbackResult,
} from "@/lib/api";

type TabKey = "guardrails" | "audit" | "checkpoints";

const TABS: { key: TabKey; label: string }[] = [
  { key: "guardrails", label: "Guardrails" },
  { key: "audit", label: "Audit" },
  { key: "checkpoints", label: "Checkpoints" },
];

export default function Security() {
  const [tab, setTab] = useState<TabKey>("guardrails");
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Security</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          B5:Guardrails / Audit events / Checkpoints + capability gating(read-only viewer +
          minimal write actions)。
        </p>
      </div>

      <div className="flex gap-1 rounded-lg border border-border p-1 w-fit">
        {TABS.map((t) => (
          <Button
            key={t.key}
            size="sm"
            variant={tab === t.key ? "default" : "ghost"}
            onClick={() => setTab(t.key)}
            className="h-7 px-3 text-xs"
          >
            {t.label}
          </Button>
        ))}
      </div>

      {tab === "guardrails" && <GuardrailsPanel />}
      {tab === "audit" && <AuditPanel />}
      {tab === "checkpoints" && <CheckpointsPanel />}
    </section>
  );
}

// ============================================================================
// Guardrails tab
// ============================================================================

function GuardrailsPanel() {
  const [rules, setRules] = useState<GuardrailRule[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [toolDraft, setToolDraft] = useState("shell_exec");
  const [argsDraft, setArgsDraft] = useState('{"command":"rm -rf /"}');
  const [verdict, setVerdict] = useState<GuardrailVerdict | null>(null);
  const [tryErr, setTryErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      const res = await api.listGuardrails();
      setRules(res.rules);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runTry = useCallback(async () => {
    setTryErr(null);
    setBusy(true);
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(argsDraft);
      if (typeof args !== "object" || args === null || Array.isArray(args)) {
        throw new Error("args must be a JSON object");
      }
    } catch (e) {
      setTryErr(`invalid args JSON: ${e instanceof Error ? e.message : String(e)}`);
      setBusy(false);
      return;
    }
    try {
      const v = await api.tryGuardrail({ tool_name: toolDraft, args });
      setVerdict(v);
    } catch (e) {
      setTryErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [argsDraft, toolDraft]);

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border p-4 space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Test rule (dry-run · 不消耗配额)
        </h2>
        <div className="grid gap-3 md:grid-cols-[12rem_1fr_auto] md:items-end">
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">tool_name</label>
            <Input value={toolDraft} onChange={(e) => setToolDraft(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">args (JSON)</label>
            <Textarea
              value={argsDraft}
              onChange={(e) => setArgsDraft(e.target.value)}
              rows={3}
              className="font-mono text-xs"
            />
          </div>
          <Button onClick={runTry} disabled={busy}>
            {busy ? "Evaluating..." : "Try"}
          </Button>
        </div>
        {tryErr && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {tryErr}
          </div>
        )}
        {verdict && (
          <div className="rounded-md border border-border bg-muted/20 p-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <VerdictBadge verdict={verdict.verdict} />
              <span className="font-mono">{verdict.rule_id}</span>
              {verdict.quota_remaining !== null && (
                <Badge variant="outline">quota: {verdict.quota_remaining}</Badge>
              )}
              {verdict.quota_exhausted && <Badge variant="destructive">quota exhausted</Badge>}
            </div>
            <p className="text-sm">{verdict.reason}</p>
            {verdict.matched_pattern && (
              <p className="text-xs font-mono text-muted-foreground">
                matched: {verdict.matched_pattern}
              </p>
            )}
          </div>
        )}
      </div>

      <section className="space-y-3">
        <SectionHeader
          title="Rules"
          subtitle="13 built-in regex rules + daily quota. Read-only here."
          count={rules.length}
        />
        {loadErr && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {loadErr}
          </div>
        )}
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-56">rule_id</TableHead>
                <TableHead className="w-36">verdict</TableHead>
                <TableHead className="w-24">quota</TableHead>
                <TableHead>description</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-10 text-center text-sm text-muted-foreground">
                    No rules.
                  </TableCell>
                </TableRow>
              ) : (
                rules.map((r) => (
                  <TableRow key={r.rule_id}>
                    <TableCell className="font-mono text-xs">{r.rule_id}</TableCell>
                    <TableCell>
                      <VerdictBadge verdict={r.verdict} />
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {r.daily_quota === null
                        ? "∞"
                        : `${r.quota_remaining ?? 0}/${r.daily_quota}`}
                    </TableCell>
                    <TableCell className="text-xs">{r.description}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const variant: "default" | "outline" | "destructive" =
    verdict === "deny" ? "destructive" : verdict === "require_approval" ? "default" : "outline";
  return (
    <Badge variant={variant} className="font-mono text-[10px] uppercase">
      {verdict}
    </Badge>
  );
}

// ============================================================================
// Audit tab
// ============================================================================

function AuditPanel() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [filterType, setFilterType] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    setLoading(true);
    try {
      const res = await api.listAuditEvents({ limit: 100 });
      setEvents(res.events);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const f = filterType.trim();
    return f ? events.filter((e) => e.event_type.includes(f)) : events;
  }, [events, filterType]);

  const selectedEvent = useMemo(
    () => (selected ? events.find((e) => e.id === selected) ?? null : null),
    [events, selected],
  );

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border p-4 flex flex-wrap items-end gap-3">
        <div className="min-w-44 flex-1 space-y-1">
          <label className="text-xs uppercase tracking-wide text-muted-foreground">
            event_type filter
          </label>
          <Input
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            placeholder="e.g. tool_call_post / guardrail_verdict"
          />
        </div>
        <Button onClick={load} disabled={loading} size="sm">
          Refresh
        </Button>
        <div className="ml-auto flex flex-wrap gap-2">
          <Badge variant="outline">{filtered.length} events</Badge>
        </div>
      </div>

      {err && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {err}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_minmax(20rem,28rem)]">
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">created_at</TableHead>
                <TableHead className="w-40">event_type</TableHead>
                <TableHead className="w-28">status</TableHead>
                <TableHead>summary</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-10 text-center text-sm text-muted-foreground">
                    No audit events.
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((ev) => (
                  <TableRow
                    key={ev.id}
                    className={`cursor-pointer ${selected === ev.id ? "bg-muted/30" : ""}`}
                    onClick={() => setSelected(selected === ev.id ? null : ev.id)}
                  >
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {formatDate(ev.created_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{ev.event_type}</TableCell>
                    <TableCell className="font-mono text-xs">{ev.status || "-"}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {summarizeAudit(ev)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        <div className="rounded-lg border border-border p-4 space-y-2">
          <h3 className="text-xs uppercase tracking-wide text-muted-foreground">
            Payload
          </h3>
          {selectedEvent ? (
            <pre className="max-h-[36rem] overflow-auto rounded-md border border-border bg-muted/20 p-3 text-xs leading-6 whitespace-pre-wrap break-words">
              {JSON.stringify(selectedEvent.payload, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">Click an event to view its payload.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function summarizeAudit(ev: AuditEvent): string {
  const p = ev.payload as Record<string, unknown>;
  switch (ev.event_type) {
    case "tool_call_pre":
    case "tool_call_post": {
      const tool = String(p.tool_name ?? "?");
      const dur = p.duration_ms;
      return dur !== undefined && dur !== null ? `${tool} (${dur}ms)` : tool;
    }
    case "guardrail_verdict":
      return `${p.rule_id ?? "?"} → ${p.verdict ?? "?"}`;
    case "memory_store":
      return `${p.action ?? "?"} ${p.memory_id ?? "?"}`;
    case "checkpoint_create":
      return `${p.kind ?? "?"} ${p.name ?? "?"}`;
    case "rollback":
      return `${p.checkpoint_id ?? "?"} ok=${p.ok}`;
    default:
      return "";
  }
}

// ============================================================================
// Checkpoints tab
// ============================================================================

function CheckpointsPanel() {
  const [checkpoints, setCheckpoints] = useState<CheckpointEntry[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilityEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [createDraft, setCreateDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<
    | { kind: "create"; entry: CheckpointEntry }
    | { kind: "rollback"; id: string; result: RollbackResult }
    | { kind: "delete"; id: string }
    | null
  >(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [cp, caps] = await Promise.all([api.listCheckpoints(), api.listCapabilities()]);
      setCheckpoints(cp.checkpoints);
      setCapabilities(caps.capabilities);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = useCallback(async () => {
    const name = createDraft.trim();
    if (!name) return;
    setBusy(true);
    try {
      const res = await api.createCheckpoint(name);
      setLastResult({ kind: "create", entry: res.checkpoint });
      setCreateDraft("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [createDraft, load]);

  const rollback = useCallback(
    async (id: string) => {
      if (!window.confirm(`Rollback checkpoint ${id}? 这会回滚 git stash / sqlite / config 三段。`)) {
        return;
      }
      setBusy(true);
      try {
        const result = await api.rollbackCheckpoint(id);
        setLastResult({ kind: "rollback", id, result });
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const remove = useCallback(
    async (id: string) => {
      if (!window.confirm(`Delete checkpoint ${id}? 会 rm 落盘 sqlite/tgz + DB 记录(git stash 保留)。`)) {
        return;
      }
      setBusy(true);
      try {
        await api.deleteCheckpoint(id);
        setLastResult({ kind: "delete", id });
        await load();
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const toggleCap = useCallback(
    async (cap: CapabilityEntry) => {
      setBusy(true);
      try {
        await api.setCapability(cap.name, !cap.enabled);
        await load();
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border p-4 space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Capabilities
        </h2>
        <p className="text-xs text-muted-foreground">
          `enable_self_mod` 打开 → `self_modify_chariot` 规则从 DENY 降级为 REQUIRE_APPROVAL。
          `yolo` 这里写 DB(persist 给 sidecar);CLI `--yolo` 是 per-process 模式不入 DB。
        </p>
        <div className="flex flex-wrap gap-2">
          {capabilities.length === 0 ? (
            <p className="text-sm text-muted-foreground">No capabilities loaded.</p>
          ) : (
            capabilities.map((cap) => (
              <Button
                key={cap.name}
                variant={cap.enabled ? "default" : "outline"}
                size="sm"
                onClick={() => void toggleCap(cap)}
                disabled={busy}
                className="font-mono text-xs"
              >
                {cap.name}: {cap.enabled ? "enabled" : "disabled"}
              </Button>
            ))
          )}
        </div>
      </section>

      <section className="rounded-lg border border-border p-4 space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Create checkpoint
        </h2>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-44 flex-1 space-y-1">
            <label className="text-xs uppercase tracking-wide text-muted-foreground">name</label>
            <Input
              value={createDraft}
              onChange={(e) => setCreateDraft(e.target.value)}
              placeholder="before_refactor"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void create();
                }
              }}
            />
          </div>
          <Button onClick={create} disabled={busy || !createDraft.trim()}>
            {busy ? "..." : "Create"}
          </Button>
        </div>
      </section>

      {err && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {err}
        </div>
      )}

      {lastResult && <LastResultBlock result={lastResult} />}

      <section className="space-y-3">
        <SectionHeader
          title="Checkpoints"
          subtitle="三件套 snapshot:git stash + sqlite backup + config tarball。"
          count={checkpoints.length}
        />
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">created_at</TableHead>
                <TableHead className="w-40">name</TableHead>
                <TableHead className="w-16 text-center">git</TableHead>
                <TableHead className="w-16 text-center">db</TableHead>
                <TableHead className="w-16 text-center">cfg</TableHead>
                <TableHead className="text-right">actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {checkpoints.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                    No checkpoints yet.
                  </TableCell>
                </TableRow>
              ) : (
                checkpoints.map((cp) => {
                  const p = cp.payload as Record<string, unknown>;
                  return (
                    <TableRow key={cp.id}>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {formatDate(cp.created_at)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{cp.name}</TableCell>
                      <TableCell className="text-center text-xs">
                        {p.git_ok ? "ok" : "skip"}
                      </TableCell>
                      <TableCell className="text-center text-xs">
                        {p.db_ok ? "ok" : "skip"}
                      </TableCell>
                      <TableCell className="text-center text-xs">
                        {p.config_ok ? "ok" : "skip"}
                      </TableCell>
                      <TableCell className="text-right space-x-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() => void rollback(cp.id)}
                          disabled={busy}
                        >
                          Rollback
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() => void remove(cp.id)}
                          disabled={busy}
                        >
                          Delete
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
    </div>
  );
}

function LastResultBlock({
  result,
}: {
  result:
    | { kind: "create"; entry: CheckpointEntry }
    | { kind: "rollback"; id: string; result: RollbackResult }
    | { kind: "delete"; id: string };
}) {
  if (result.kind === "create") {
    const p = result.entry.payload as Record<string, unknown>;
    return (
      <div className="rounded-md border border-border bg-muted/20 p-3 space-y-2 text-xs">
        <div className="flex flex-wrap gap-2">
          <Badge variant="default">created</Badge>
          <span className="font-mono">{result.entry.id}</span>
          <span className="font-mono">{result.entry.name}</span>
        </div>
        <pre className="overflow-auto rounded border border-border bg-background p-2 leading-6 whitespace-pre-wrap break-words">
          {JSON.stringify(p, null, 2)}
        </pre>
      </div>
    );
  }
  if (result.kind === "rollback") {
    const r = result.result;
    return (
      <div className="rounded-md border border-border bg-muted/20 p-3 space-y-2 text-xs">
        <div className="flex flex-wrap gap-2">
          <Badge variant="default">rollback</Badge>
          <span className="font-mono">{result.id}</span>
          <Badge variant={r.git_ok ? "outline" : "destructive"}>git: {r.git_ok ? "ok" : "fail"}</Badge>
          <Badge variant={r.db_ok ? "outline" : "destructive"}>db: {r.db_ok ? "ok" : "fail"}</Badge>
          <Badge variant={r.config_ok ? "outline" : "destructive"}>
            config: {r.config_ok ? "ok" : "fail"}
          </Badge>
        </div>
        {r.restored.length > 0 && (
          <div>
            <span className="font-semibold">restored:</span>
            <ul className="ml-4 list-disc">
              {r.restored.map((s, i) => (
                <li key={i} className="font-mono">{s}</li>
              ))}
            </ul>
          </div>
        )}
        {r.errors.length > 0 && (
          <div className="text-destructive">
            <span className="font-semibold">errors:</span>
            <ul className="ml-4 list-disc">
              {r.errors.map((s, i) => (
                <li key={i} className="font-mono">{s}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3 text-xs">
      <Badge variant="destructive">deleted</Badge> <span className="font-mono">{result.id}</span>
    </div>
  );
}

// ============================================================================
// Shared
// ============================================================================

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
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h2>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>
      <Badge variant="outline">{count}</Badge>
    </div>
  );
}

function formatDate(value: string): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
