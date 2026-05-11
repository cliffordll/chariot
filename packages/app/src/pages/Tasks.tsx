import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  type AgentProfile,
  type ApiError,
  type TaskDetail,
  type TaskEntry,
  type TaskRun,
} from "@/lib/api";

type TasksState =
  | { kind: "loading" }
  | { kind: "ok"; tasks: TaskEntry[]; agents: AgentProfile[] }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; taskId: string }
  | { kind: "ok"; task: TaskDetail }
  | { kind: "err"; taskId: string; message: string };

type CreateDialogState = { open: false } | { open: true };

export default function Tasks() {
  const [state, setState] = useState<TasksState>({ kind: "loading" });
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [createDialog, setCreateDialog] = useState<CreateDialogState>({ open: false });
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (preferredTaskId?: string | null) => {
    setState({ kind: "loading" });
    try {
      const [{ tasks }, { agents }] = await Promise.all([api.listTasks(), api.listAgents()]);
      setState({ kind: "ok", tasks, agents });
      const selectedTaskId =
        preferredTaskId ??
        (detail.kind === "ok" ? detail.task.id : detail.kind === "loading" || detail.kind === "err" ? detail.taskId : null) ??
        tasks[0]?.id ??
        null;
      if (selectedTaskId) {
        void loadTaskDetail(selectedTaskId);
      } else {
        setDetail({ kind: "idle" });
      }
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message });
      setDetail({ kind: "idle" });
    }
  }, [detail.kind, detail]);

  const loadTaskDetail = useCallback(async (taskId: string) => {
    setDetail({ kind: "loading", taskId });
    try {
      const { task } = await api.getTask(taskId);
      setDetail({ kind: "ok", task });
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail({ kind: "err", taskId, message });
    }
  }, []);

  const runTaskAction = useCallback(
    async (taskId: string, action: "pause" | "resume" | "cancel") => {
      setNotice(null);
      try {
        if (action === "pause") await api.pauseTask(taskId);
        if (action === "resume") await api.resumeTask(taskId);
        if (action === "cancel") await api.cancelTask(taskId);
        await load(taskId);
      } catch (e) {
        setNotice(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  const runStart = useCallback(async (taskId: string) => {
    setNotice(null);
    try {
      await api.startTaskRun({ task_id: taskId, trigger: "ui" });
      await load(taskId);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  }, [load]);

  const runFinish = useCallback(
    async (run: TaskRun, mode: "complete" | "fail" | "cancel") => {
      setNotice(null);
      try {
        if (mode === "complete") {
          await api.completeTaskRun({ run_id: run.id, result: { source: "ui" } });
        }
        if (mode === "fail") {
          await api.failTaskRun({ run_id: run.id, error: "failed from ui", result: { source: "ui" } });
        }
        if (mode === "cancel") {
          await api.cancelTaskRun({ run_id: run.id, error: "cancelled from ui", result: { source: "ui" } });
        }
        await load(run.task_id);
      } catch (e) {
        setNotice(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Tasks</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage task state, inspect child summaries, and control task runs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setCreateDialog({ open: true })}>
            + Create task
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            Refresh
          </Button>
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-amber-300/30 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {notice}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <TasksListCard state={state} selectedTaskId={detail.kind === "ok" ? detail.task.id : detail.kind === "loading" || detail.kind === "err" ? detail.taskId : null} onSelect={loadTaskDetail} />
        <TaskDetailCard detail={detail} onTaskAction={runTaskAction} onStartRun={runStart} onFinishRun={runFinish} />
      </div>

      {createDialog.open && (
        <CreateTaskDialog
          agents={state.kind === "ok" ? state.agents : []}
          onClose={() => setCreateDialog({ open: false })}
          onCreated={(taskId) => {
            setCreateDialog({ open: false });
            void load(taskId);
          }}
        />
      )}
    </section>
  );
}

function TasksListCard({
  state,
  selectedTaskId,
  onSelect,
}: {
  state: TasksState;
  selectedTaskId: string | null;
  onSelect: (taskId: string) => void;
}) {
  if (state.kind === "loading") {
    return <Panel title="Task Queue">Loading tasks...</Panel>;
  }
  if (state.kind === "err") {
    return <Panel title="Task Queue" tone="danger">{state.message}</Panel>;
  }
  return (
    <Panel title="Task Queue" subtitle={`${state.tasks.length} tasks`}>
      {state.tasks.length === 0 ? (
        <p className="text-sm text-muted-foreground">No tasks yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Status</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Goal</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.tasks.map((task) => (
              <TableRow
                key={task.id}
                data-state={task.id === selectedTaskId ? "selected" : undefined}
                className="cursor-pointer"
                onClick={() => onSelect(task.id)}
              >
                <TableCell><TaskStatusBadge status={task.status} /></TableCell>
                <TableCell><code className="text-xs">{task.kind}</code></TableCell>
                <TableCell>{task.agent_profile ?? "-"}</TableCell>
                <TableCell className="max-w-[18rem] truncate">{task.goal}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

function TaskDetailCard({
  detail,
  onTaskAction,
  onStartRun,
  onFinishRun,
}: {
  detail: DetailState;
  onTaskAction: (taskId: string, action: "pause" | "resume" | "cancel") => void;
  onStartRun: (taskId: string) => void;
  onFinishRun: (run: TaskRun, mode: "complete" | "fail" | "cancel") => void;
}) {
  if (detail.kind === "idle") {
    return <Panel title="Task Detail">Select a task to inspect runs and controls.</Panel>;
  }
  if (detail.kind === "loading") {
    return <Panel title="Task Detail">Loading {detail.taskId}...</Panel>;
  }
  if (detail.kind === "err") {
    return <Panel title="Task Detail" tone="danger">{detail.message}</Panel>;
  }

  const task = detail.task;
  const activeRun = task.runs.find((run) => run.status === "running") ?? null;

  return (
    <Panel title="Task Detail" subtitle={task.id}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <TaskStatusBadge status={task.status} />
              <Badge variant="outline">{task.kind}</Badge>
              {task.agent_profile && <Badge variant="secondary">{task.agent_profile}</Badge>}
            </div>
            <div className="text-lg font-medium">{task.goal}</div>
            <div className="grid gap-1 text-sm text-muted-foreground">
              <span>owner: {task.owner ?? "-"}</span>
              <span>parent: {task.parent_task_id ?? "-"}</span>
              <span>updated: {formatDateTime(task.updated_at)}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => onTaskAction(task.id, "pause")}>Pause</Button>
            <Button variant="outline" size="sm" onClick={() => onTaskAction(task.id, "resume")}>Resume</Button>
            <Button variant="outline" size="sm" onClick={() => onTaskAction(task.id, "cancel")}>Cancel</Button>
            <Button size="sm" onClick={() => onStartRun(task.id)}>Start run</Button>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <MetaBlock title="Child status summary" value={task.child_status_summary} />
          <MetaBlock title="Task meta" value={task.meta} />
        </div>

        {activeRun && (
          <div className="rounded-lg border border-border bg-muted/20 p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium">Active run</div>
                <div className="text-xs text-muted-foreground">{activeRun.id}</div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={() => onFinishRun(activeRun, "complete")}>Complete</Button>
                <Button variant="outline" size="sm" onClick={() => onFinishRun(activeRun, "fail")}>Fail</Button>
                <Button variant="outline" size="sm" onClick={() => onFinishRun(activeRun, "cancel")}>Cancel</Button>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              trigger: {activeRun.trigger} · started: {formatDateTime(activeRun.started_at)}
            </div>
          </div>
        )}

        <div>
          <div className="mb-2 text-sm font-medium">Runs</div>
          {task.runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No runs recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Finished</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {task.runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell><RunStatusBadge status={run.status} /></TableCell>
                    <TableCell>{run.trigger}</TableCell>
                    <TableCell>{formatDateTime(run.started_at)}</TableCell>
                    <TableCell>{formatDateTime(run.finished_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </div>
    </Panel>
  );
}

function CreateTaskDialog({
  agents,
  onClose,
  onCreated,
}: {
  agents: AgentProfile[];
  onClose: () => void;
  onCreated: (taskId: string) => void;
}) {
  const [goal, setGoal] = useState("");
  const [kind, setKind] = useState<TaskEntry["kind"]>("interactive");
  const [agentProfile, setAgentProfile] = useState("");
  const [owner, setOwner] = useState("");
  const [meta, setMeta] = useState("{}");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    let parsedMeta: Record<string, unknown> = {};
    try {
      parsedMeta = parseJsonObject(meta);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSubmitting(true);
    try {
      const { task } = await api.createTask({
        goal,
        kind,
        agent_profile: agentProfile || null,
        owner: owner || null,
        meta: parsedMeta,
      });
      onCreated(task.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Create task</DialogTitle>
          <DialogDescription>Queue a new task with an optional agent profile and owner.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Goal">
            <Input value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Investigate task orchestration" />
          </Field>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Kind">
              <Input value={kind} onChange={(e) => setKind(e.target.value as TaskEntry["kind"])} />
            </Field>
            <Field label="Owner">
              <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="user" />
            </Field>
          </div>
          <Field label="Agent profile">
            <Input
              list="task-agent-profiles"
              value={agentProfile}
              onChange={(e) => setAgentProfile(e.target.value)}
              placeholder={agents[0]?.name ?? "optional"}
            />
            <datalist id="task-agent-profiles">
              {agents.map((agent) => (
                <option key={agent.name} value={agent.name} />
              ))}
            </datalist>
          </Field>
          <Field label="Meta JSON">
            <Textarea value={meta} onChange={(e) => setMeta(e.target.value)} className="min-h-28 font-mono text-xs" />
          </Field>
          {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={submitting}>{submitting ? "Creating..." : "Create"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function MetaBlock({ title, value }: { title: string; value: Record<string, unknown> }) {
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      <div className="mb-2 text-sm font-medium">{title}</div>
      <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-all rounded bg-background/80 p-3 text-xs">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function TaskStatusBadge({ status }: { status: TaskEntry["status"] }) {
  const variant = status === "completed" ? "default" : status === "failed" || status === "cancelled" ? "destructive" : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

function RunStatusBadge({ status }: { status: TaskRun["status"] }) {
  const variant = status === "completed" ? "default" : status === "failed" || status === "cancelled" ? "destructive" : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function parseJsonObject(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const value = JSON.parse(trimmed);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("meta must be a JSON object");
  }
  return value as Record<string, unknown>;
}
