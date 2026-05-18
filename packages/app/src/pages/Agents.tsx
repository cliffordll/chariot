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
import { Textarea } from "@/components/ui/textarea";
import { api, type AgentProfile, type ApiError, type PromptBundle, type Provider, type Toolset } from "@/lib/api";

type AgentsState =
  | { kind: "loading" }
  | { kind: "ok"; agents: AgentProfile[] }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; name: string }
  | { kind: "ok"; agent: AgentProfile }
  | { kind: "err"; name: string; message: string };

type BindingOptions = {
  bundles: PromptBundle[];
  toolsets: Toolset[];
  providers: Provider[];
};

type BindingOption = {
  value: string;
  label: string;
};

function bindingText(label: string | null, ref: string | null): string {
  return label ?? ref ?? "-";
}

function BindingStack({ agent }: { agent: AgentProfile }) {
  const rows = [
    { label: "Prompt", text: bindingText(agent.prompt_label, agent.prompt_id), variant: "outline" as const },
    { label: "Toolset", text: bindingText(agent.toolset_label, agent.toolset_id), variant: "secondary" as const },
    { label: "Provider", text: bindingText(agent.provider_label, agent.provider_id), variant: "outline" as const },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-1">
      {rows.map((row) => (
        <div key={row.label} className="flex min-w-0 items-center gap-2">
          <span className="w-14 shrink-0 text-[11px] font-medium text-muted-foreground">{row.label}</span>
          <Badge variant={row.variant} className="min-w-0 max-w-full truncate">
            {row.text}
          </Badge>
        </div>
      ))}
    </div>
  );
}

export default function Agents() {
  const [state, setState] = useState<AgentsState>({ kind: "loading" });
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [createOpen, setCreateOpen] = useState(false);
  const [editAgent, setEditAgent] = useState<AgentProfile | null>(null);
  const [removeAgent, setRemoveAgent] = useState<AgentProfile | null>(null);
  const [removing, setRemoving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [bindingOptions, setBindingOptions] = useState<BindingOptions>({ bundles: [], toolsets: [], providers: [] });

  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const loadAgent = useCallback(async (name: string) => {
    setDetail({ kind: "loading", name });
    try {
      const { agent } = await api.getAgent(name);
      setDetail({ kind: "ok", agent });
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail({ kind: "err", name, message });
    }
  }, []);

  const load = useCallback(async (preferredName?: string | null) => {
    setState({ kind: "loading" });
    try {
      const { agents } = await api.listAgents();
      setState({ kind: "ok", agents });
      const d = detailRef.current;
      const currentSelected =
        d.kind === "ok" ? d.agent.name :
        d.kind === "loading" || d.kind === "err" ? d.name :
        null;
      const selected = preferredName ?? currentSelected ?? agents[0]?.name ?? null;
      if (selected) {
        void loadAgent(selected);
      } else {
        setDetail({ kind: "idle" });
      }
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message });
      setDetail({ kind: "idle" });
    }
  }, [loadAgent]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void (async () => {
      try {
        const [{ bundles }, { toolsets }, { providers }] = await Promise.all([
          api.listPromptBundles(),
          api.listToolsets(),
          api.listProviders(),
        ]);
        setBindingOptions({ bundles, toolsets, providers });
      } catch {
        // 静默:datalist 是辅助提示,失败不阻断 Agents 页主功能
      }
    })();
  }, []);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Agents</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage agent profiles, role bundles, and provider or tool bindings.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setCreateOpen(true)}>+ Add agent</Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>Refresh</Button>
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-amber-300/30 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {notice}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,1fr)]">
        <AgentsListCard state={state} selectedName={detail.kind === "ok" ? detail.agent.name : detail.kind === "loading" || detail.kind === "err" ? detail.name : null} onSelect={loadAgent} />
        <AgentDetailCard
          detail={detail}
          onEdit={(agent) => setEditAgent(agent)}
          onRemove={(agent) => setRemoveAgent(agent)}
        />
      </div>

      {createOpen && (
        <CreateAgentDialog
          options={bindingOptions}
          onClose={() => setCreateOpen(false)}
          onCreated={(name) => {
            setCreateOpen(false);
            setNotice(`Created agent profile ${name}.`);
            void load(name);
          }}
        />
      )}

      {editAgent && (
        <EditAgentDialog
          agent={editAgent}
          options={bindingOptions}
          onClose={() => setEditAgent(null)}
          onSaved={(name) => {
            setEditAgent(null);
            setNotice(`Updated agent profile ${name}.`);
            void load(name);
          }}
        />
      )}

      <AlertDialog open={!!removeAgent} onOpenChange={(open) => { if (!open && !removing) setRemoveAgent(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove agent profile</AlertDialogTitle>
            <AlertDialogDescription>
              {removeAgent ? `Permanently delete profile "${removeAgent.name}"? This cannot be undone.` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={removing}
              onClick={async (e) => {
                e.preventDefault();
                if (!removeAgent) return;
                setRemoving(true);
                try {
                  await api.deleteAgent(removeAgent.name);
                  const name = removeAgent.name;
                  detailRef.current = { kind: "idle" };
                  setDetail({ kind: "idle" });
                  setRemoveAgent(null);
                  setNotice(`Removed agent profile ${name}.`);
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

function AgentsListCard({
  state,
  selectedName,
  onSelect,
}: {
  state: AgentsState;
  selectedName: string | null;
  onSelect: (name: string) => void;
}) {
  if (state.kind === "loading") return <Panel title="Agent Profiles">Loading agents...</Panel>;
  if (state.kind === "err") return <Panel title="Agent Profiles" tone="danger">{state.message}</Panel>;

  return (
    <Panel title="Agent Profiles" subtitle={`${state.agents.length} profiles`}>
      {state.agents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No agent profiles yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Bindings</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.agents.map((agent) => (
              <TableRow
                key={agent.name}
                data-state={selectedName === agent.name ? "selected" : undefined}
                className="cursor-pointer"
                onClick={() => onSelect(agent.name)}
              >
                <TableCell className="font-medium">{agent.name}</TableCell>
                <TableCell>{agent.role}</TableCell>
                <TableCell className="max-w-[18rem] align-top">
                  <BindingStack agent={agent} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

function AgentDetailCard({
  detail,
  onEdit,
  onRemove,
}: {
  detail: DetailState;
  onEdit: (agent: AgentProfile) => void;
  onRemove: (agent: AgentProfile) => void;
}) {
  if (detail.kind === "idle") return <Panel title="Agent Detail">Select an agent profile to inspect its bindings.</Panel>;
  if (detail.kind === "loading") return <Panel title="Agent Detail">Loading {detail.name}...</Panel>;
  if (detail.kind === "err") return <Panel title="Agent Detail" tone="danger">{detail.message}</Panel>;

  const agent = detail.agent;
  return (
    <Panel title="Agent Detail" subtitle={agent.name}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{agent.role}</Badge>
              {agent.prompt_id && <Badge variant="outline">{bindingText(agent.prompt_label, agent.prompt_id)}</Badge>}
              {agent.toolset_id && <Badge variant="secondary">{bindingText(agent.toolset_label, agent.toolset_id)}</Badge>}
              {agent.provider_id && <Badge variant="outline">{bindingText(agent.provider_label, agent.provider_id)}</Badge>}
              {agent.reflection_enabled && (
                <Badge variant="default">reflect ×{agent.reflection_max_retries}</Badge>
              )}
            </div>
            <div className="grid gap-1 text-sm text-muted-foreground">
              <span>created: {formatDateTime(agent.created_at)}</span>
              <span>updated: {formatDateTime(agent.updated_at)}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => onEdit(agent)}>Edit</Button>
            <Button variant="outline" size="sm" onClick={() => onRemove(agent)}>Remove</Button>
          </div>
        </div>

        <MetaBlock title="Budget" value={agent.budget} />
        <MetaBlock title="Meta" value={agent.meta} />
      </div>
    </Panel>
  );
}

const BINDING_NONE = "__none__";

function BindingSelect({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: BindingOption[];
  placeholder: string;
}) {
  return (
    <Select
      value={value === "" ? BINDING_NONE : value}
      onValueChange={(v) => onChange(v === BINDING_NONE ? "" : v)}
    >
      <SelectTrigger className="h-9 w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={BINDING_NONE}>(none)</SelectItem>
        {options.map((opt) => (
          <SelectItem key={opt.value} value={opt.value}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function CreateAgentDialog({
  options,
  onClose,
  onCreated,
}: {
  options: BindingOptions;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [promptId, setPromptId] = useState("");
  const [toolsetId, setToolsetId] = useState("");
  const [providerProfile, setProviderProfile] = useState("");
  const [budget, setBudget] = useState("{}");
  const [meta, setMeta] = useState("{}");
  const [reflectionEnabled, setReflectionEnabled] = useState(false);
  const [reflectionMaxRetries, setReflectionMaxRetries] = useState(2);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    let parsedBudget: Record<string, unknown>;
    let parsedMeta: Record<string, unknown>;
    try {
      parsedBudget = parseJsonObject(budget);
      parsedMeta = parseJsonObject(meta);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSubmitting(true);
    try {
      await api.createAgent({
        name,
        role,
        prompt_id: promptId || null,
        toolset_id: toolsetId || null,
        provider_id: providerProfile || null,
        budget: parsedBudget,
        meta: parsedMeta,
        reflection_enabled: reflectionEnabled,
        reflection_max_retries: reflectionMaxRetries,
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
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add agent profile</DialogTitle>
          <DialogDescription>
            Define a reusable profile for tasks and delegated work.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="planner" />
            </Field>
            <Field label="Role">
              <Input value={role} onChange={(e) => setRole(e.target.value)} placeholder="planner" />
            </Field>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Prompt ID">
                <BindingSelect
                value={promptId}
                onChange={setPromptId}
                options={options.bundles.map((bundle) => ({ value: bundle.id, label: bundle.name }))}
                placeholder="(none)"
              />
            </Field>
            <Field label="Toolset ID">
              <BindingSelect
                value={toolsetId}
                onChange={setToolsetId}
                options={options.toolsets.map((toolset) => ({ value: toolset.id, label: toolset.name }))}
                placeholder="(none)"
              />
            </Field>
            <Field label="Provider profile">
              <BindingSelect
                value={providerProfile}
                onChange={setProviderProfile}
                options={options.providers.map((provider) => ({ value: provider.id, label: provider.name }))}
                placeholder="(none)"
              />
            </Field>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Budget JSON">
              <Textarea value={budget} onChange={(e) => setBudget(e.target.value)} className="min-h-28 font-mono text-xs" />
            </Field>
            <Field label="Meta JSON">
              <Textarea value={meta} onChange={(e) => setMeta(e.target.value)} className="min-h-28 font-mono text-xs" />
            </Field>
          </div>
          <div className="grid gap-3 md:grid-cols-2 items-end">
            <Field label="Reflection (B4)">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={reflectionEnabled}
                  onChange={(e) => setReflectionEnabled(e.target.checked)}
                />
                <span>Enable reflect-then-retry</span>
              </label>
            </Field>
            <Field label="Reflection max retries">
              <Input
                type="number"
                min={0}
                max={20}
                value={reflectionMaxRetries}
                onChange={(e) => setReflectionMaxRetries(parseInt(e.target.value, 10) || 0)}
                disabled={!reflectionEnabled}
              />
            </Field>
          </div>
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

function EditAgentDialog({
  agent,
  options,
  onClose,
  onSaved,
}: {
  agent: AgentProfile;
  options: BindingOptions;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [name, setName] = useState(agent.name);
  const [role, setRole] = useState(agent.role);
  const [promptId, setPromptId] = useState(agent.prompt_id ?? "");
  const [toolsetId, setToolsetId] = useState(agent.toolset_id ?? "");
  const [providerProfile, setProviderProfile] = useState(agent.provider_id ?? "");
  const [budget, setBudget] = useState(JSON.stringify(agent.budget, null, 2));
  const [meta, setMeta] = useState(JSON.stringify(agent.meta, null, 2));
  const [reflectionEnabled, setReflectionEnabled] = useState(agent.reflection_enabled);
  const [reflectionMaxRetries, setReflectionMaxRetries] = useState(agent.reflection_max_retries);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    let parsedBudget: Record<string, unknown>;
    let parsedMeta: Record<string, unknown>;
    try {
      parsedBudget = parseJsonObject(budget);
      parsedMeta = parseJsonObject(meta);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSubmitting(true);
    try {
      await api.updateAgent(agent.name, {
        rename: name.trim() !== agent.name ? name.trim() : undefined,
        role,
        prompt_id: promptId || null,
        toolset_id: toolsetId || null,
        provider_id: providerProfile || null,
        budget: parsedBudget,
        meta: parsedMeta,
        reflection_enabled: reflectionEnabled,
        reflection_max_retries: reflectionMaxRetries,
      });
      onSaved(name.trim() || agent.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit agent profile</DialogTitle>
          <DialogDescription>Update role, bindings, budget, or meta for {agent.name}.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Role">
            <Input value={role} onChange={(e) => setRole(e.target.value)} />
          </Field>
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Prompt ID">
              <BindingSelect
                value={promptId}
                onChange={setPromptId}
                options={options.bundles.map((bundle) => ({ value: bundle.id, label: bundle.name }))}
                placeholder="(none)"
              />
            </Field>
            <Field label="Toolset ID">
              <BindingSelect
                value={toolsetId}
                onChange={setToolsetId}
                options={options.toolsets.map((toolset) => ({ value: toolset.id, label: toolset.name }))}
                placeholder="(none)"
              />
            </Field>
            <Field label="Provider profile">
              <BindingSelect
                value={providerProfile}
                onChange={setProviderProfile}
                options={options.providers.map((provider) => ({ value: provider.id, label: provider.name }))}
                placeholder="(none)"
              />
            </Field>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Budget JSON">
              <Textarea value={budget} onChange={(e) => setBudget(e.target.value)} className="min-h-28 font-mono text-xs" />
            </Field>
            <Field label="Meta JSON">
              <Textarea value={meta} onChange={(e) => setMeta(e.target.value)} className="min-h-28 font-mono text-xs" />
            </Field>
          </div>
          <div className="grid gap-3 md:grid-cols-2 items-end">
            <Field label="Reflection (B4)">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={reflectionEnabled}
                  onChange={(e) => setReflectionEnabled(e.target.checked)}
                />
                <span>Enable reflect-then-retry</span>
              </label>
            </Field>
            <Field label="Reflection max retries">
              <Input
                type="number"
                min={0}
                max={20}
                value={reflectionMaxRetries}
                onChange={(e) => setReflectionMaxRetries(parseInt(e.target.value, 10) || 0)}
                disabled={!reflectionEnabled}
              />
            </Field>
          </div>
          {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={submitting}>{submitting ? "Saving..." : "Save changes"}</Button>
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
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-background/80 p-3 text-xs">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function parseJsonObject(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const value = JSON.parse(trimmed);
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("value must be a JSON object");
  }
  return value as Record<string, unknown>;
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString();
}
