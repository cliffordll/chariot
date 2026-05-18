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
import { api, type ApiError, type Tool, type Toolset } from "@/lib/api";

type ToolsetsState =
  | { kind: "loading" }
  | { kind: "ok"; toolsets: Toolset[] }
  | { kind: "err"; message: string };

type DetailState =
  | { kind: "idle" }
  | { kind: "loading"; name: string }
  | { kind: "ok"; toolset: Toolset }
  | { kind: "err"; name: string; message: string };

export default function Toolsets() {
  const [state, setState] = useState<ToolsetsState>({ kind: "loading" });
  const [detail, setDetail] = useState<DetailState>({ kind: "idle" });
  const [tools, setTools] = useState<Tool[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Toolset | null>(null);
  const [removeTarget, setRemoveTarget] = useState<Toolset | null>(null);
  const [removing, setRemoving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const detailRef = useRef(detail);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const loadOne = useCallback(async (name: string) => {
    setDetail({ kind: "loading", name });
    try {
      const { toolset } = await api.getToolset(name);
      setDetail({ kind: "ok", toolset });
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail({ kind: "err", name, message });
    }
  }, []);

  const load = useCallback(async (preferredName?: string | null) => {
    setState({ kind: "loading" });
    try {
      const [{ toolsets }, { tools: toolList }] = await Promise.all([api.listToolsets(), api.listTools()]);
      setState({ kind: "ok", toolsets });
      setTools(toolList);
      const d = detailRef.current;
      const currentSelected =
        d.kind === "ok" ? d.toolset.name :
        d.kind === "loading" || d.kind === "err" ? d.name :
        null;
      const selected = preferredName ?? currentSelected ?? toolsets[0]?.name ?? null;
      if (selected) {
        void loadOne(selected);
      } else {
        setDetail({ kind: "idle" });
      }
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message });
      setDetail({ kind: "idle" });
    }
  }, [loadOne]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Toolsets</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Group tools into named toolsets; agent profiles reference one by name to filter the runtime tool list.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setCreateOpen(true)}>+ Add toolset</Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>Refresh</Button>
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-amber-300/30 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {notice}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,1fr)]">
        <ToolsetsListCard state={state} selectedName={selectedName(detail)} onSelect={loadOne} />
        <ToolsetDetailCard
          detail={detail}
          tools={tools}
          onEdit={(toolset) => setEditTarget(toolset)}
          onRemove={(toolset) => setRemoveTarget(toolset)}
          onAddMember={async (name, tool_name) => {
            try {
              await api.addToolsetMember(name, tool_name);
              await loadOne(name);
              setNotice(`Added ${tool_name} to ${name}.`);
            } catch (e) {
              setNotice(e instanceof Error ? e.message : String(e));
            }
          }}
          onRemoveMember={async (name, tool_name) => {
            try {
              await api.removeToolsetMember(name, tool_name);
              await loadOne(name);
              setNotice(`Removed ${tool_name} from ${name}.`);
            } catch (e) {
              setNotice(e instanceof Error ? e.message : String(e));
            }
          }}
        />
      </div>

      {createOpen && (
        <CreateToolsetDialog
          tools={tools}
          onClose={() => setCreateOpen(false)}
          onCreated={(name) => {
            setCreateOpen(false);
            setNotice(`Created toolset ${name}.`);
            void load(name);
          }}
        />
      )}

      {editTarget && (
        <EditToolsetDialog
          toolset={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={(name) => {
            setEditTarget(null);
            setNotice(`Updated toolset ${name}.`);
            void load(name);
          }}
        />
      )}

      <AlertDialog open={!!removeTarget} onOpenChange={(open) => { if (!open && !removing) setRemoveTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove toolset</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget ? `Permanently delete toolset "${removeTarget.name}"? Members will be cleared too. This cannot be undone.` : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={removing}
              onClick={async (e) => {
                e.preventDefault();
                if (!removeTarget) return;
                setRemoving(true);
                try {
                  await api.deleteToolset(removeTarget.name);
                  const name = removeTarget.name;
                  detailRef.current = { kind: "idle" };
                  setDetail({ kind: "idle" });
                  setRemoveTarget(null);
                  setNotice(`Removed toolset ${name}.`);
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

function selectedName(detail: DetailState): string | null {
  if (detail.kind === "ok") return detail.toolset.name;
  if (detail.kind === "loading" || detail.kind === "err") return detail.name;
  return null;
}

function ToolsetsListCard({
  state,
  selectedName,
  onSelect,
}: {
  state: ToolsetsState;
  selectedName: string | null;
  onSelect: (name: string) => void;
}) {
  if (state.kind === "loading") return <Panel title="Toolsets">Loading toolsets...</Panel>;
  if (state.kind === "err") return <Panel title="Toolsets" tone="danger">{state.message}</Panel>;

  return (
    <Panel title="Toolsets" subtitle={`${state.toolsets.length} toolsets`}>
      {state.toolsets.length === 0 ? (
        <p className="text-sm text-muted-foreground">No toolsets yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Members</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.toolsets.map((ts) => (
              <TableRow
                key={ts.name}
                data-state={selectedName === ts.name ? "selected" : undefined}
                className="cursor-pointer"
                onClick={() => onSelect(ts.name)}
              >
                <TableCell className="font-medium">{ts.name}</TableCell>
                <TableCell>{ts.description ?? "-"}</TableCell>
                <TableCell>{ts.members.length}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Panel>
  );
}

function ToolsetDetailCard({
  detail,
  tools,
  onEdit,
  onRemove,
  onAddMember,
  onRemoveMember,
}: {
  detail: DetailState;
  tools: Tool[];
  onEdit: (toolset: Toolset) => void;
  onRemove: (toolset: Toolset) => void;
  onAddMember: (name: string, tool_name: string) => Promise<void>;
  onRemoveMember: (name: string, tool_name: string) => Promise<void>;
}) {
  const [pendingMember, setPendingMember] = useState("");

  if (detail.kind === "idle") return <Panel title="Toolset Detail">Select a toolset to inspect its members.</Panel>;
  if (detail.kind === "loading") return <Panel title="Toolset Detail">Loading {detail.name}...</Panel>;
  if (detail.kind === "err") return <Panel title="Toolset Detail" tone="danger">{detail.message}</Panel>;

  const toolset = detail.toolset;
  const candidates = tools.filter((t) => !toolset.members.includes(t.name));

  return (
    <Panel title="Toolset Detail" subtitle={toolset.name}>
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{toolset.name}</Badge>
              {toolset.description && <span className="text-sm text-muted-foreground">{toolset.description}</span>}
            </div>
            <div className="grid gap-1 text-sm text-muted-foreground">
              <span>created: {formatDateTime(toolset.created_at)}</span>
              <span>updated: {formatDateTime(toolset.updated_at)}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => onEdit(toolset)}>Edit</Button>
            <Button variant="outline" size="sm" onClick={() => onRemove(toolset)}>Remove</Button>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-muted/10 p-4">
          <div className="mb-2 text-sm font-medium">Members ({toolset.members.length})</div>
          {toolset.members.length === 0 ? (
            <p className="text-sm text-muted-foreground">No members. Add a tool below.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {toolset.members.map((m) => (
                <Badge key={m} variant="secondary" className="flex items-center gap-1">
                  {m}
                  <button
                    type="button"
                    className="ml-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => void onRemoveMember(toolset.name, m)}
                    aria-label={`remove ${m}`}
                  >
                    ×
                  </button>
                </Badge>
              ))}
            </div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              list="toolset-tool-options"
              value={pendingMember}
              onChange={(e) => setPendingMember(e.target.value)}
              placeholder="tool name"
              className="max-w-xs"
            />
            <datalist id="toolset-tool-options">
              {candidates.map((t) => (
                <option key={t.name} value={t.name} />
              ))}
            </datalist>
            <Button
              size="sm"
              variant="outline"
              disabled={!pendingMember.trim()}
              onClick={async () => {
                const tn = pendingMember.trim();
                if (!tn) return;
                await onAddMember(toolset.name, tn);
                setPendingMember("");
              }}
            >
              + Add member
            </Button>
          </div>
        </div>

        <MetaBlock title="Meta" value={toolset.meta} />
      </div>
    </Panel>
  );
}

function CreateToolsetDialog({
  tools,
  onClose,
  onCreated,
}: {
  tools: Tool[];
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [members, setMembers] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await api.createToolset({
        name: name.trim(),
        description: description.trim() || null,
        members: members.split(",").map((s) => s.trim()).filter(Boolean),
      });
      onCreated(name.trim());
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
          <DialogTitle>Add toolset</DialogTitle>
          <DialogDescription>Group tools into a named set; reference it from an agent profile.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="fs_safe" />
          </Field>
          <Field label="Description">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="read-only fs access" />
          </Field>
          <Field label="Members (comma-separated)">
            <Input
              list="create-toolset-tool-options"
              value={members}
              onChange={(e) => setMembers(e.target.value)}
              placeholder="read_file, list_dir"
            />
            <datalist id="create-toolset-tool-options">
              {tools.map((t) => (
                <option key={t.name} value={t.name} />
              ))}
            </datalist>
          </Field>
          {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={submitting || !name.trim()}>
            {submitting ? "Creating..." : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditToolsetDialog({
  toolset,
  onClose,
  onSaved,
}: {
  toolset: Toolset;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [name, setName] = useState(toolset.name);
  const [description, setDescription] = useState(toolset.description ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await api.updateToolset(toolset.name, {
        rename: name.trim() !== toolset.name ? name.trim() : undefined,
        description: description.trim() || null,
      });
      onSaved(name.trim() || toolset.name);
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
          <DialogTitle>Edit toolset</DialogTitle>
          <DialogDescription>Update description for {toolset.name}. Manage members from the detail panel.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Description">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
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

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}
