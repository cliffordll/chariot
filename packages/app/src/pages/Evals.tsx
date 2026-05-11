import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  type DiffResult,
  type DiffStatus,
  type EvalRunListEntry,
  type EvalRunSnapshot,
  type EvalVerdict,
  type GoldenTaskEntry,
} from "@/lib/api";

type TabKey = "tasks" | "runs";

type TasksState =
  | { kind: "loading" }
  | { kind: "ok"; tasks: GoldenTaskEntry[]; golden_dir: string; missing: boolean }
  | { kind: "err"; message: string };

type RunsState =
  | { kind: "loading" }
  | { kind: "ok"; runs: EvalRunListEntry[]; runs_dir: string }
  | { kind: "err"; message: string };

type SnapshotState =
  | { kind: "idle" }
  | { kind: "loading"; run_id: string }
  | { kind: "ok"; snapshot: EvalRunSnapshot }
  | { kind: "err"; run_id: string; message: string };

type DiffState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; diff: DiffResult }
  | { kind: "err"; message: string };

const NO_BASELINE = "__none__";

const VERDICT_TONE: Record<EvalVerdict, string> = {
  PASS: "bg-emerald-500/15 text-emerald-700 border-emerald-500/30",
  FAIL: "bg-rose-500/15 text-rose-700 border-rose-500/30",
  ERROR: "bg-amber-500/15 text-amber-700 border-amber-500/30",
  SKIP: "bg-slate-400/15 text-slate-600 border-slate-400/30",
};

const DIFF_TONE: Record<DiffStatus, string> = {
  NEW: "bg-sky-500/15 text-sky-700 border-sky-500/30",
  REMOVED: "bg-slate-400/15 text-slate-600 border-slate-400/30",
  REGRESSED: "bg-rose-500/15 text-rose-700 border-rose-500/30",
  RECOVERED: "bg-emerald-500/15 text-emerald-700 border-emerald-500/30",
  CHANGED: "bg-amber-500/15 text-amber-700 border-amber-500/30",
  STABLE: "bg-slate-300/10 text-slate-500 border-slate-300/30",
};

export default function Evals() {
  const [tab, setTab] = useState<TabKey>("runs");
  const [tasksState, setTasksState] = useState<TasksState>({ kind: "loading" });
  const [runsState, setRunsState] = useState<RunsState>({ kind: "loading" });
  const [snapshot, setSnapshot] = useState<SnapshotState>({ kind: "idle" });
  const [baselineId, setBaselineId] = useState<string>("");
  const [diff, setDiff] = useState<DiffState>({ kind: "idle" });

  const loadTasks = useCallback(async () => {
    setTasksState({ kind: "loading" });
    try {
      const data = await api.listGoldenTasks();
      setTasksState({ kind: "ok", ...data });
    } catch (e) {
      setTasksState({ kind: "err", message: extractErr(e) });
    }
  }, []);

  const loadRuns = useCallback(async () => {
    setRunsState({ kind: "loading" });
    try {
      const data = await api.listEvalRuns();
      setRunsState({ kind: "ok", ...data });
    } catch (e) {
      setRunsState({ kind: "err", message: extractErr(e) });
    }
  }, []);

  const openRun = useCallback(async (run_id: string) => {
    setSnapshot({ kind: "loading", run_id });
    setDiff({ kind: "idle" });
    try {
      const snap = await api.getEvalRun(run_id);
      setSnapshot({ kind: "ok", snapshot: snap });
    } catch (e) {
      setSnapshot({ kind: "err", run_id, message: extractErr(e) });
    }
  }, []);

  useEffect(() => {
    void loadTasks();
    void loadRuns();
  }, [loadTasks, loadRuns]);

  const runDiff = useCallback(async () => {
    if (snapshot.kind !== "ok" || !baselineId || baselineId === NO_BASELINE) return;
    setDiff({ kind: "loading" });
    try {
      const result = await api.diffEvalRuns(baselineId, snapshot.snapshot.run_id);
      setDiff({ kind: "ok", diff: result });
    } catch (e) {
      setDiff({ kind: "err", message: extractErr(e) });
    }
  }, [baselineId, snapshot]);

  const baselineOptions = useMemo(() => {
    if (runsState.kind !== "ok") return [];
    if (snapshot.kind !== "ok") return runsState.runs;
    return runsState.runs.filter((r) => r.run_id !== snapshot.snapshot.run_id);
  }, [runsState, snapshot]);

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Evals</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            B2 golden task evaluation 跑批结果 · 跨 run baseline diff
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TabButton current={tab} value="runs" onClick={() => setTab("runs")}>Runs</TabButton>
          <TabButton current={tab} value="tasks" onClick={() => setTab("tasks")}>Tasks</TabButton>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              if (tab === "runs") void loadRuns();
              else void loadTasks();
            }}
          >
            Refresh
          </Button>
        </div>
      </div>

      {tab === "tasks" ? (
        <TasksPanel state={tasksState} />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,1fr)]">
          <RunsList
            state={runsState}
            selectedId={snapshot.kind === "ok" ? snapshot.snapshot.run_id : snapshot.kind === "loading" || snapshot.kind === "err" ? snapshot.run_id : null}
            onSelect={openRun}
          />
          <RunDetail
            snapshot={snapshot}
            baselineId={baselineId}
            onBaselineChange={setBaselineId}
            baselineOptions={baselineOptions}
            onRunDiff={runDiff}
            diff={diff}
          />
        </div>
      )}
    </section>
  );
}

function TasksPanel({ state }: { state: TasksState }) {
  if (state.kind === "loading") return <Panel title="Golden tasks">Loading…</Panel>;
  if (state.kind === "err") return <Panel title="Golden tasks" tone="danger">{state.message}</Panel>;
  if (state.missing) {
    return (
      <Panel title="Golden tasks" subtitle={state.golden_dir}>
        <p className="text-sm text-muted-foreground">
          目录不存在:{state.golden_dir}。sidecar 默认从 cwd 找 tests/golden/,跑桌面端时若 cwd 不在 chariot 仓库根,可能找不到。
        </p>
      </Panel>
    );
  }
  if (state.tasks.length === 0) {
    return <Panel title="Golden tasks" subtitle={state.golden_dir}>(没有 task,目录为空)</Panel>;
  }
  return (
    <Panel title="Golden tasks" subtitle={`${state.tasks.length} task · ${state.golden_dir}`}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>task_id</TableHead>
            <TableHead>category</TableHead>
            <TableHead>verifier</TableHead>
            <TableHead>description</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.tasks.map((t) => (
            <TableRow key={t.task_id}>
              <TableCell className="font-mono text-xs">{t.task_id}</TableCell>
              <TableCell>{t.category}</TableCell>
              <TableCell><Badge variant="outline">{t.verifier_type}</Badge></TableCell>
              <TableCell className="text-sm text-muted-foreground">{t.description || "-"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}

function RunsList({
  state,
  selectedId,
  onSelect,
}: {
  state: RunsState;
  selectedId: string | null;
  onSelect: (run_id: string) => void;
}) {
  if (state.kind === "loading") return <Panel title="Runs">Loading…</Panel>;
  if (state.kind === "err") return <Panel title="Runs" tone="danger">{state.message}</Panel>;
  if (state.runs.length === 0) {
    return (
      <Panel title="Runs" subtitle={state.runs_dir}>
        <p className="text-sm text-muted-foreground">
          还没有 eval run 落过盘。CLI 跑 `uv run chariot eval` 后会落到这里。
        </p>
      </Panel>
    );
  }
  return (
    <Panel title="Runs" subtitle={`${state.runs.length} · ${state.runs_dir}`}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>run_id</TableHead>
            <TableHead>pass</TableHead>
            <TableHead>agent</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.runs.map((r) => {
            const summary = r.summary;
            const passText = summary ? `${summary.passed}/${summary.total} · ${(summary.pass_rate * 100).toFixed(0)}%` : "-";
            return (
              <TableRow
                key={r.run_id}
                data-state={selectedId === r.run_id ? "selected" : undefined}
                className="cursor-pointer"
                onClick={() => onSelect(r.run_id)}
              >
                <TableCell className="font-mono text-xs">{r.run_id}</TableCell>
                <TableCell>{passText}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {(r.meta?.agent_profile as string | null) || "-"}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Panel>
  );
}

function RunDetail({
  snapshot,
  baselineId,
  onBaselineChange,
  baselineOptions,
  onRunDiff,
  diff,
}: {
  snapshot: SnapshotState;
  baselineId: string;
  onBaselineChange: (value: string) => void;
  baselineOptions: EvalRunListEntry[];
  onRunDiff: () => void;
  diff: DiffState;
}) {
  if (snapshot.kind === "idle") return <Panel title="Run detail">选一个 run 查看 verdict / cost / tool 记录。</Panel>;
  if (snapshot.kind === "loading") return <Panel title="Run detail">Loading {snapshot.run_id}…</Panel>;
  if (snapshot.kind === "err") return <Panel title="Run detail" tone="danger">{snapshot.message}</Panel>;

  const snap = snapshot.snapshot;
  const summary = snap.summary;

  return (
    <Panel title="Run detail" subtitle={snap.run_id}>
      <div className="space-y-5">
        <div className="grid gap-1 text-sm">
          <div>
            <span className="text-muted-foreground">passed:</span>{" "}
            {summary?.passed ?? "-"} / {summary?.total ?? "-"}{" "}
            {summary && <span className="text-muted-foreground">({(summary.pass_rate * 100).toFixed(0)}%)</span>}
          </div>
          <div>
            <span className="text-muted-foreground">tokens:</span>{" "}
            in={summary?.total_input_tokens ?? 0} out={summary?.total_output_tokens ?? 0}{" "}
            <span className="text-muted-foreground">· cost=${(summary?.total_cost_usd ?? 0).toFixed(4)}</span>
          </div>
          {snap.meta?.agent_profile && (
            <div><span className="text-muted-foreground">agent_profile:</span> {String(snap.meta.agent_profile)}</div>
          )}
        </div>

        <div className="flex flex-wrap items-end gap-2 border-t pt-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted-foreground">Baseline run</label>
            <Select
              value={baselineId || NO_BASELINE}
              onValueChange={(v) => onBaselineChange(v === NO_BASELINE ? "" : v)}
            >
              <SelectTrigger className="h-8 w-56">
                <SelectValue placeholder="(none)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_BASELINE}>(none)</SelectItem>
                {baselineOptions.map((r) => (
                  <SelectItem key={r.run_id} value={r.run_id}>{r.run_id}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            size="sm"
            onClick={onRunDiff}
            disabled={!baselineId || baselineId === NO_BASELINE || diff.kind === "loading"}
          >
            {diff.kind === "loading" ? "Diffing…" : "Diff vs baseline"}
          </Button>
        </div>

        {diff.kind === "err" && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {diff.message}
          </div>
        )}

        {diff.kind === "ok" && <DiffView diff={diff.diff} />}

        <RecordsTable records={snap.records} />
      </div>
    </Panel>
  );
}

function DiffView({ diff }: { diff: DiffResult }) {
  const visible = diff.entries.filter((e) => e.status !== "STABLE");
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      <div className="mb-2 text-sm font-medium">
        diff vs {diff.baseline_id}{" "}
        <span className="ml-2 text-xs text-muted-foreground">
          changes={diff.summary.total_changes} ·
          regressed={diff.summary.regressed} · recovered={diff.summary.recovered} ·
          new={diff.summary.new} · removed={diff.summary.removed} · changed={diff.summary.changed}
        </span>
      </div>
      {visible.length === 0 ? (
        <p className="text-sm text-muted-foreground">(无变化)</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {visible.map((e) => (
            <li key={e.task_id} className="flex items-center gap-2">
              <span className={`rounded border px-2 py-0.5 text-xs ${DIFF_TONE[e.status]}`}>{e.status}</span>
              <span className="font-mono text-xs">{e.task_id}</span>
              <span className="text-xs text-muted-foreground">({e.baseline_verdict ?? "-"} → {e.current_verdict ?? "-"})</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RecordsTable({ records }: { records: EvalRunSnapshot["records"] }) {
  if (records.length === 0) return null;
  return (
    <div>
      <div className="mb-2 text-sm font-medium">records</div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>task_id</TableHead>
            <TableHead>verdict</TableHead>
            <TableHead>cost</TableHead>
            <TableHead>turn</TableHead>
            <TableHead>reason</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {records.map((r) => (
            <TableRow key={r.task_id}>
              <TableCell className="font-mono text-xs">{r.task_id}</TableCell>
              <TableCell>
                <span className={`rounded border px-2 py-0.5 text-xs ${VERDICT_TONE[r.verdict]}`}>{r.verdict}</span>
              </TableCell>
              <TableCell className="text-xs">
                ${r.cost_usd.toFixed(4)}{" "}
                <span className="text-muted-foreground">· {r.input_tokens}/{r.output_tokens}</span>
              </TableCell>
              <TableCell>
                {r.turn_id ? (
                  <Link to={`/traces?turn=${r.turn_id}`} className="font-mono text-xs text-primary hover:underline">
                    {r.turn_id.slice(-8)}
                  </Link>
                ) : (
                  <span className="text-xs text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell className="max-w-md truncate text-xs text-muted-foreground" title={r.reason}>
                {r.reason || "-"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function TabButton({
  current,
  value,
  onClick,
  children,
}: {
  current: TabKey;
  value: TabKey;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <Button
      variant={active ? "default" : "outline"}
      size="sm"
      onClick={onClick}
    >
      {children}
    </Button>
  );
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

function extractErr(e: unknown): string {
  if (e instanceof Error) {
    const apiErr = e as ApiError;
    return apiErr.message || e.message;
  }
  return String(e);
}
