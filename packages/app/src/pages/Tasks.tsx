import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CollapsibleJson } from "@/components/collapsible-json";
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

type DelegateDialogState = { open: false } | { open: true; parentTask: TaskEntry };

export default function Tasks() {
  const [state, setState] = useState<TasksState>({ kind: "loading" });
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [createDialog, setCreateDialog] = useState<CreateDialogState>({ open: false });
  const [delegateDialog, setDelegateDialog] = useState<DelegateDialogState>({ open: false });
  const [notice, setNotice] = useState<string | null>(null);

  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

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

  const load = useCallback(async (preferredTaskId?: string | null) => {
    setState({ kind: "loading" });
    try {
      const [{ tasks }, { agents }] = await Promise.all([api.listTasks(), api.listAgents()]);
      setState({ kind: "ok", tasks, agents });
      const d = detailRef.current;
      const currentSelected =
        d.kind === "ok" ? d.task.id :
        d.kind === "loading" || d.kind === "err" ? d.taskId :
        null;
      const selectedTaskId = preferredTaskId ?? currentSelected ?? tasks[0]?.id ?? null;
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
  }, [loadTaskDetail]);

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
        <TaskDetailCard
          detail={detail}
          onTaskAction={runTaskAction}
          onStartRun={runStart}
          onFinishRun={runFinish}
          onDelegate={(task) => setDelegateDialog({ open: true, parentTask: task })}
        />
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

      {delegateDialog.open && (
        <DelegateTaskDialog
          parentTask={delegateDialog.parentTask}
          agents={state.kind === "ok" ? state.agents : []}
          onClose={() => setDelegateDialog({ open: false })}
          onDelegated={(count) => {
            const parentId = delegateDialog.parentTask.id;
            setDelegateDialog({ open: false });
            setNotice(`Delegated ${count} child task(s).`);
            void load(parentId);
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
  const tasksById = new Map(state.tasks.map((t) => [t.id, t]));
  const childrenByParent = new Map<string, TaskEntry[]>();
  const roots: TaskEntry[] = [];
  for (const t of state.tasks) {
    if (t.parent_task_id && tasksById.has(t.parent_task_id)) {
      const arr = childrenByParent.get(t.parent_task_id) ?? [];
      arr.push(t);
      childrenByParent.set(t.parent_task_id, arr);
    } else {
      roots.push(t);
    }
  }
  const ordered: { task: TaskEntry; depth: number; childCount: number; isLastChild: boolean }[] = [];
  for (const root of roots) {
    const children = childrenByParent.get(root.id) ?? [];
    ordered.push({ task: root, depth: 0, childCount: children.length, isLastChild: false });
    children.forEach((child, i) => {
      ordered.push({
        task: child,
        depth: 1,
        childCount: childrenByParent.get(child.id)?.length ?? 0,
        isLastChild: i === children.length - 1,
      });
    });
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
            {ordered.map(({ task, depth, childCount, isLastChild }) => {
              const isChild = depth > 0;
              return (
                <TableRow
                  key={task.id}
                  data-state={task.id === selectedTaskId ? "selected" : undefined}
                  className={`cursor-pointer ${isChild ? "bg-muted/30" : ""}`}
                  onClick={() => onSelect(task.id)}
                >
                  <TableCell className="relative">
                    {isChild && (
                      <span
                        aria-hidden
                        className={`pointer-events-none absolute left-2 top-0 w-3 border-l-2 border-primary/40 ${
                          isLastChild ? "h-1/2" : "h-full"
                        }`}
                      />
                    )}
                    {isChild && (
                      <span
                        aria-hidden
                        className="pointer-events-none absolute left-2 top-1/2 h-0 w-3 border-t-2 border-primary/40"
                      />
                    )}
                    <span className={isChild ? "ml-6" : ""}>
                      <TaskStatusBadge status={task.status} />
                    </span>
                  </TableCell>
                  <TableCell><code className="text-xs">{task.kind}</code></TableCell>
                  <TableCell>{task.agent_profile ?? "-"}</TableCell>
                  <TableCell className="max-w-[20rem]">
                    <div className="flex items-center gap-2">
                      <span className="truncate">{task.goal}</span>
                      {childCount > 0 && (
                        <Badge className="ml-auto shrink-0 text-xs">
                          {childCount} {childCount === 1 ? "child" : "children"}
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
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
  onDelegate,
}: {
  detail: DetailState;
  onTaskAction: (taskId: string, action: "pause" | "resume" | "cancel") => void;
  onStartRun: (taskId: string) => void;
  onFinishRun: (run: TaskRun, mode: "complete" | "fail" | "cancel") => void;
  onDelegate: (task: TaskEntry) => void;
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
            <Button variant="outline" size="sm" onClick={() => onDelegate(task)}>Delegate</Button>
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

function DelegateTaskDialog({
  parentTask,
  agents,
  onClose,
  onDelegated,
}: {
  parentTask: TaskEntry;
  agents: AgentProfile[];
  onClose: () => void;
  onDelegated: (count: number) => void;
}) {
  const [goals, setGoals] = useState<string[]>([""]);
  const [reason, setReason] = useState("");
  const [agentProfile, setAgentProfile] = useState(parentTask.agent_profile ?? "");
  const [owner, setOwner] = useState(parentTask.owner ?? "");
  const [meta, setMeta] = useState("{}");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    const cleanedGoals = goals.map((g) => g.trim()).filter(Boolean);
    if (cleanedGoals.length === 0) {
      setError("At least one non-empty goal is required.");
      return;
    }
    let parsedMeta: Record<string, unknown> = {};
    try {
      parsedMeta = parseJsonObject(meta);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSubmitting(true);
    try {
      const { delegation } = await api.delegateTask({
        parent_task_id: parentTask.id,
        tasks: cleanedGoals.map((goal) => ({
          goal,
          agent_profile: agentProfile || null,
          owner: owner || null,
        })),
        reason: reason || null,
        meta: parsedMeta,
      });
      onDelegated(delegation.created);
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
          <DialogTitle>Delegate child tasks</DialogTitle>
          <DialogDescription>Create one or more child tasks under {parentTask.id}.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2">
            <Label>Goals</Label>
            {goals.map((goal, i) => (
              <div key={i} className="flex gap-2">
                <Input
                  value={goal}
                  onChange={(e) => {
                    const next = [...goals];
                    next[i] = e.target.value;
                    setGoals(next);
                  }}
                  placeholder={`child task ${i + 1}`}
                />
                {goals.length > 1 && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setGoals(goals.filter((_, idx) => idx !== i))}
                  >
                    −
                  </Button>
                )}
              </div>
            ))}
            <Button type="button" variant="outline" size="sm" onClick={() => setGoals([...goals, ""])}>
              + Add goal
            </Button>
          </div>
          <Field label="Reason">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="split work" />
          </Field>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Agent profile (applies to all)">
              <Input
                list="delegate-agent-profiles"
                value={agentProfile}
                onChange={(e) => setAgentProfile(e.target.value)}
                placeholder="optional"
              />
              <datalist id="delegate-agent-profiles">
                {agents.map((agent) => (
                  <option key={agent.name} value={agent.name} />
                ))}
              </datalist>
            </Field>
            <Field label="Owner (applies to all)">
              <Input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="optional" />
            </Field>
          </div>
          <Field label="Meta JSON">
            <Textarea value={meta} onChange={(e) => setMeta(e.target.value)} className="min-h-20 font-mono text-xs" />
          </Field>
          {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={submitting}>{submitting ? "Delegating..." : "Delegate"}</Button>
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
  return <CollapsibleJson title={title} value={value} maxHeightClassName="max-h-44" />;
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
