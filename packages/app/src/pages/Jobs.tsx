import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
  type ScheduledJob,
  type ScheduledJobDetail,
} from "@/lib/api";

type JobsState =
  | { kind: "loading" }
  | { kind: "ok"; jobs: ScheduledJob[]; agents: AgentProfile[] }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; name: string }
  | { kind: "ok"; job: ScheduledJobDetail }
  | { kind: "err"; name: string; message: string };

export default function Jobs() {
  const [state, setState] = useState<JobsState>({ kind: "loading" });
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [createOpen, setCreateOpen] = useState(false);
  const [removeJob, setRemoveJob] = useState<ScheduledJob | null>(null);
  const [removing, setRemoving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const loadJobDetail = useCallback(async (name: string) => {
    setDetail({ kind: "loading", name });
    try {
      const { job } = await api.showJob(name);
      setDetail({ kind: "ok", job });
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail({ kind: "err", name, message });
    }
  }, []);

  const load = useCallback(async (preferredJob?: string | null) => {
    setState({ kind: "loading" });
    try {
      const [{ jobs }, { agents }] = await Promise.all([api.listJobs(), api.listAgents()]);
      setState({ kind: "ok", jobs, agents });
      const d = detailRef.current;
      const currentSelected =
        d.kind === "ok" ? d.job.name :
        d.kind === "loading" || d.kind === "err" ? d.name :
        null;
      const selected = preferredJob ?? currentSelected ?? jobs[0]?.name ?? null;
      if (selected) {
        void loadJobDetail(selected);
      } else {
        setDetail({ kind: "idle" });
      }
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message });
      setDetail({ kind: "idle" });
    }
  }, [loadJobDetail]);

  const runJobAction = useCallback(async (name: string, action: "enable" | "disable" | "run") => {
    setNotice(null);
    try {
      if (action === "enable") await api.enableJob(name);
      if (action === "disable") await api.disableJob(name);
      if (action === "run") await api.runJobNow(name);
      await load(name);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Jobs</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Review scheduled jobs, update cron metadata, and trigger jobs manually.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setCreateOpen(true)}>+ Add job</Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>Refresh</Button>
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-amber-300/30 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {notice}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(20rem,0.95fr)]">
        <JobsListCard state={state} selectedName={detail.kind === "ok" ? detail.job.name : detail.kind === "loading" || detail.kind === "err" ? detail.name : null} onSelect={loadJobDetail} />
        <JobDetailCard
          detail={detail}
          onAction={runJobAction}
          onSaved={(name) => void load(name)}
          onRemove={(job) => setRemoveJob(job)}
        />
      </div>

      {createOpen && (
        <CreateJobDialog
          agents={state.kind === "ok" ? state.agents : []}
          onClose={() => setCreateOpen(false)}
          onCreated={(name) => {
            setCreateOpen(false);
            void load(name);
          }}
        />
      )}

      <AlertDialog open={!!removeJob} onOpenChange={(open) => { if (!open && !removing) setRemoveJob(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove scheduled job</AlertDialogTitle>
            <AlertDialogDescription>
              {removeJob ? `Permanently delete job "${removeJob.name}"? This cannot be undone.` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={removing}
              onClick={async (e) => {
                e.preventDefault();
                if (!removeJob) return;
                setRemoving(true);
                try {
                  await api.deleteJob(removeJob.name);
                  const name = removeJob.name;
                  detailRef.current = { kind: "idle" };
                  setDetail({ kind: "idle" });
                  setRemoveJob(null);
                  setNotice(`Removed job ${name}.`);
                  void load();
                } catch (err) {
                  setNotice(err instanceof Error ? err.message : String(err));
                } finally {
                  setRemoving(false);
                }
              }}
            >
              {removing ? "Removing..." : "Remove"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function JobsListCard({
  state,
  selectedName,
  onSelect,
}: {
  state: JobsState;
  selectedName: string | null;
  onSelect: (name: string) => void;
}) {
  if (state.kind === "loading") return <Panel title="Jobs">Loading jobs...</Panel>;
  if (state.kind === "err") return <Panel title="Jobs" tone="danger">{state.message}</Panel>;
  return (
    <Panel title="Jobs" subtitle={`${state.jobs.length} registered`}>
      {state.jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No scheduled jobs yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Enabled</TableHead>
              <TableHead>Cron</TableHead>
              <TableHead>Last run</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.jobs.map((job) => (
              <TableRow
                key={job.name}
                data-state={job.name === selectedName ? "selected" : undefined}
                className="cursor-pointer"
                onClick={() => onSelect(job.name)}
              >
                <TableCell className="font-medium">{job.name}</TableCell>
                <TableCell><Badge variant={job.enabled ? "default" : "secondary"}>{job.enabled ? "enabled" : "disabled"}</Badge></TableCell>
                <TableCell><code className="text-xs">{job.cron}</code></TableCell>
                <TableCell>{job.last_run_status ?? "-"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

function JobDetailCard({
  detail,
  onAction,
  onSaved,
  onRemove,
}: {
  detail: DetailState;
  onAction: (name: string, action: "enable" | "disable" | "run") => void;
  onSaved: (name: string) => void;
  onRemove: (job: ScheduledJob) => void;
}) {
  if (detail.kind === "idle") return <Panel title="Job Detail">Select a job to inspect runs and settings.</Panel>;
  if (detail.kind === "loading") return <Panel title="Job Detail">Loading {detail.name}...</Panel>;
  if (detail.kind === "err") return <Panel title="Job Detail" tone="danger">{detail.message}</Panel>;

  const job = detail.job;

  return (
    <Panel title="Job Detail" subtitle={job.name}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={job.enabled ? "default" : "secondary"}>{job.enabled ? "enabled" : "disabled"}</Badge>
              {job.agent_profile && <Badge variant="outline">{job.agent_profile}</Badge>}
            </div>
            <div className="text-lg font-medium">{job.goal}</div>
            <div className="grid gap-1 text-sm text-muted-foreground">
              <span>cron: <code>{job.cron}</code></span>
              <span>last run: {job.last_run_status ?? "-"} at {formatDateTime(job.last_run_at)}</span>
              <span>next run: {formatDateTime(job.next_run_at)}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => onAction(job.name, "enable")}>Enable</Button>
            <Button variant="outline" size="sm" onClick={() => onAction(job.name, "disable")}>Disable</Button>
            <Button variant="outline" size="sm" onClick={() => onRemove(job)}>Remove</Button>
            <Button size="sm" onClick={() => onAction(job.name, "run")}>Run now</Button>
          </div>
        </div>

        <EditJobCard job={job} onSaved={onSaved} />

        <MetaBlock title="Job meta" value={job.meta} />

        <div>
          <div className="mb-2 text-sm font-medium">Run history</div>
          {job.runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No job runs recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Task</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Finished</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {job.runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>{run.status}</TableCell>
                    <TableCell>{run.task_id ?? "-"}</TableCell>
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

function EditJobCard({
  job,
  onSaved,
}: {
  job: ScheduledJobDetail;
  onSaved: (name: string) => void;
}) {
  const [goal, setGoal] = useState(job.goal);
  const [cron, setCron] = useState(job.cron);
  const [agentProfile, setAgentProfile] = useState(job.agent_profile ?? "");
  const [meta, setMeta] = useState(JSON.stringify(job.meta, null, 2));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setGoal(job.goal);
    setCron(job.cron);
    setAgentProfile(job.agent_profile ?? "");
    setMeta(JSON.stringify(job.meta, null, 2));
    setError(null);
  }, [job]);

  const submit = async () => {
    setError(null);
    let parsedMeta: Record<string, unknown>;
    try {
      parsedMeta = parseJsonObject(meta);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSaving(true);
    try {
      await api.updateJob(job.name, {
        goal,
        cron,
        agent_profile: agentProfile || null,
        meta: parsedMeta,
      });
      onSaved(job.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-muted/10 p-4">
      <div className="mb-3 text-sm font-medium">Edit job</div>
      <div className="space-y-3">
        <Field label="Goal">
          <Input value={goal} onChange={(e) => setGoal(e.target.value)} />
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Cron">
            <Input value={cron} onChange={(e) => setCron(e.target.value)} />
          </Field>
          <Field label="Agent profile">
            <Input value={agentProfile} onChange={(e) => setAgentProfile(e.target.value)} placeholder="optional" />
          </Field>
        </div>
        <Field label="Meta JSON">
          <Textarea value={meta} onChange={(e) => setMeta(e.target.value)} className="min-h-28 font-mono text-xs" />
        </Field>
        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
        <div className="flex justify-end">
          <Button size="sm" onClick={() => void submit()} disabled={saving}>{saving ? "Saving..." : "Save changes"}</Button>
        </div>
      </div>
    </div>
  );
}

function CreateJobDialog({
  agents,
  onClose,
  onCreated,
}: {
  agents: AgentProfile[];
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [cron, setCron] = useState("0 * * * *");
  const [agentProfile, setAgentProfile] = useState("");
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
      await api.createJob({
        name,
        goal,
        cron,
        agent_profile: agentProfile || null,
        meta: parsedMeta,
      });
      onCreated(name);
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
          <DialogTitle>Add job</DialogTitle>
          <DialogDescription>Create a scheduled job entry that can be triggered manually or by a scheduler later.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="cleanup" />
          </Field>
          <Field label="Goal">
            <Input value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="cleanup stale state" />
          </Field>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Cron">
              <Input value={cron} onChange={(e) => setCron(e.target.value)} />
            </Field>
            <Field label="Agent profile">
              <Input
                list="job-agent-profiles"
                value={agentProfile}
                onChange={(e) => setAgentProfile(e.target.value)}
                placeholder={agents[0]?.name ?? "optional"}
              />
              <datalist id="job-agent-profiles">
                {agents.map((agent) => (
                  <option key={agent.name} value={agent.name} />
                ))}
              </datalist>
            </Field>
          </div>
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
