import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

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
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  type ApiError,
  type ContextBundle,
  type ContextBundleDetail,
  type ContextVersion,
} from "@/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; bundles: ContextBundle[] }
  | { kind: "err"; message: string };

type BundleDialogMode =
  | { kind: "closed" }
  | { kind: "add" }
  | { kind: "edit"; bundle: ContextBundle };

export default function Context() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ContextBundleDetail | null>(null);
  const [versions, setVersions] = useState<ContextVersion[]>([]);
  const [expandedVersionId, setExpandedVersionId] = useState<string | null>(null);
  const [bundleDialog, setBundleDialog] = useState<BundleDialogMode>({ kind: "closed" });

  const loadBundles = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const { bundles } = await api.listContextBundles();
      setState({ kind: "ok", bundles });
      setSelected((cur) => {
        if (cur && bundles.some((bundle) => bundle.name === cur)) return cur;
        return bundles[0]?.name ?? null;
      });
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message });
    }
  }, []);

  const loadSelected = useCallback(async (name: string) => {
    try {
      const [bundleRes, versionsRes] = await Promise.all([
        api.getContextBundle(name),
        api.listContextVersions(name),
      ]);
      setDetail(bundleRes.bundle);
      setVersions(versionsRes.versions);
      setExpandedVersionId(null);
    } catch (e) {
      const message = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail(null);
      setVersions([]);
      setExpandedVersionId(null);
      setState({ kind: "err", message });
    }
  }, []);

  useEffect(() => {
    void loadBundles();
  }, [loadBundles]);

  useEffect(() => {
    if (selected) void loadSelected(selected);
  }, [selected, loadSelected]);

  const selectedBundle = useMemo(
    () => (state.kind === "ok" ? state.bundles.find((bundle) => bundle.name === selected) ?? null : null),
    [state, selected],
  );

  const refreshAll = useCallback(async () => {
    await loadBundles();
    if (selected) await loadSelected(selected);
  }, [loadBundles, loadSelected, selected]);

  const activateBundle = useCallback(
    async (name: string, version?: string) => {
      await api.activateContextBundle({ name, version });
      await refreshAll();
    },
    [refreshAll],
  );

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Context</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage context bundles and inspect runtime snapshots.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setBundleDialog({ kind: "add" })}>
            + New bundle
          </Button>
          <Button variant="outline" size="sm" onClick={() => void refreshAll()}>
            Refresh
          </Button>
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
          <div className="grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)]">
            <section className="rounded-lg border border-border p-4">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Bundles
                </h2>
                <Badge variant="outline">{state.bundles.length}</Badge>
              </div>
              {state.bundles.length === 0 ? (
                <p className="text-sm text-muted-foreground">No context bundle yet.</p>
              ) : (
                <ul className="space-y-2">
                  {state.bundles.map((bundle) => {
                    const active = bundle.name === selected;
                    return (
                      <li
                        key={bundle.name}
                        className={
                          "rounded-md border px-3 py-2 transition-colors " +
                          (active ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40")
                        }
                      >
                        <button type="button" className="w-full text-left" onClick={() => setSelected(bundle.name)}>
                          <div className="flex items-center gap-2">
                            <code className="font-mono text-sm">{bundle.name}</code>
                            {bundle.is_active && <Badge>active</Badge>}
                          </div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            version {bundle.active_version ?? "-"} · {bundle.version_count} versions
                          </div>
                          <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                            {bundle.description || "No description"}
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section className="space-y-6">
              <div className="rounded-lg border border-border p-4">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-semibold">
                    {detail?.name ?? selectedBundle?.name ?? "(no selection)"}
                  </h2>
                  {detail?.is_active && <Badge>active</Badge>}
                  {detail?.active_version && <Badge variant="outline">{detail.active_version}</Badge>}
                  <div className="ml-auto flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => detail && setBundleDialog({ kind: "edit", bundle: detail })}
                      disabled={!detail}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void activateBundle(detail?.name ?? "")}
                      disabled={!detail}
                    >
                      Activate bundle
                    </Button>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Info label="description" value={detail?.description || "-"} />
                  <Info label="current version" value={detail?.active_version || "-"} />
                  <Info label="versions" value={String(detail?.version_count ?? 0)} />
                  <Info label="updated_at" value={formatDate(detail?.updated_at ?? "")} />
                </div>
              </div>

              <Card title="Versions" subtitle="Expand a version row to inspect its context policy spec.">
                <div className="overflow-hidden rounded-md border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2">version</th>
                        <th className="px-3 py-2">active</th>
                        <th className="px-3 py-2">updated</th>
                        <th className="px-3 py-2 text-right">action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {versions.map((version) => {
                        const expanded = expandedVersionId === version.id;
                        return (
                          <Fragment key={version.id}>
                            <tr className={"border-t border-border " + (expanded ? "bg-primary/5" : "")}>
                              <td className="px-3 py-2 font-mono">{version.version}</td>
                              <td className="px-3 py-2">{version.is_active ? "yes" : "no"}</td>
                              <td className="px-3 py-2 text-xs text-muted-foreground">{formatDate(version.updated_at)}</td>
                              <td className="px-3 py-2 text-right">
                                <div className="inline-flex items-center gap-2">
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => setExpandedVersionId((cur) => (cur === version.id ? null : version.id))}
                                  >
                                    {expanded ? "Hide spec" : "Show spec"}
                                  </Button>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => void activateBundle(version.bundle_name, version.version)}
                                  >
                                    Activate
                                  </Button>
                                </div>
                              </td>
                            </tr>
                            {expanded && (
                              <tr className="border-t border-border bg-muted/20">
                                <td colSpan={4} className="px-3 py-3">
                                  <pre className="overflow-auto rounded-md border border-border bg-background p-3 text-xs leading-5 whitespace-pre-wrap break-words">
                                    {JSON.stringify(version.spec, null, 2)}
                                  </pre>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            </section>
          </div>
        </div>
      )}

      {bundleDialog.kind !== "closed" && (
        <BundleDialog
          mode={bundleDialog}
          onClose={() => setBundleDialog({ kind: "closed" })}
          onSaved={async (preferredName?: string) => {
            setBundleDialog({ kind: "closed" });
            await loadBundles();
            if (preferredName) {
              setSelected(preferredName);
              await loadSelected(preferredName);
            } else {
              await refreshAll();
            }
          }}
        />
      )}
    </section>
  );
}

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="mb-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>
      {children}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm">{value}</div>
    </div>
  );
}

function BundleDialog({
  mode,
  onClose,
  onSaved,
}: {
  mode: Exclude<BundleDialogMode, { kind: "closed" }>;
  onClose: () => void;
  onSaved: (preferredName?: string) => void | Promise<void>;
}) {
  const editing = mode.kind === "edit";
  const [name, setName] = useState(editing ? mode.bundle.name : "");
  const [rename, setRename] = useState(editing ? mode.bundle.name : "");
  const [description, setDescription] = useState(editing ? (mode.bundle.description ?? "") : "");
  const [specText, setSpecText] = useState(
    JSON.stringify(
      {
        version: "v1",
        name: "default_context_policy",
        include_conversation_history: true,
        include_runtime_state: true,
        include_memory_state: true,
        include_tool_state: true,
        include_skill_state: true,
        include_provider_state: true,
        include_policy_state: true,
        trimmed: false,
      },
      null,
      2,
    ),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    let spec: Record<string, unknown> | null = null;
    try {
      const value = JSON.parse(specText.trim());
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new Error("spec must be a JSON object");
      }
      spec = value as Record<string, unknown>;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        await api.updateContextBundle({
          name,
          rename: rename.trim() !== name ? rename.trim() : null,
          description,
          spec,
        });
        await onSaved(rename.trim() || name);
      } else {
        await api.addContextBundle({
          name: name.trim(),
          description: description.trim() || null,
          spec,
        });
        await onSaved(name.trim());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit context bundle" : "New context bundle"}</DialogTitle>
          <DialogDescription>
            {editing ? "Update bundle metadata and create a new version spec." : "Create a versioned context bundle."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Name</Label>
              <Input
                value={editing ? rename : name}
                onChange={(e) => (editing ? setRename(e.target.value) : setName(e.target.value))}
                placeholder="default"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Description</Label>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Context policy bundle" />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>Spec JSON</Label>
            <Textarea value={specText} onChange={(e) => setSpecText(e.target.value)} className="min-h-80 font-mono text-xs" />
          </div>
          {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? (editing ? "Saving..." : "Creating...") : editing ? "Save changes" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function formatDate(value: string): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
