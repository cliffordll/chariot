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
import { api, type ApiError, type PromptBundle, type PromptTrace, type PromptVersion } from "@/lib/api";

const DEFAULT_LAYER_TEMPLATE = [
  { name: "base_system", source: "manual", content: "You are Chariot. Be direct, accurate, and concise." },
  { name: "developer", source: "manual", content: "Prefer clear structure and concrete steps." },
  { name: "runtime", source: "manual", content: "Use the current conversation context." },
  { name: "memory", source: "manual", content: null },
  { name: "skill", source: "manual", content: null },
  { name: "tool_instruction", source: "manual", content: null },
  { name: "tool_choice", source: "manual", content: null },
  { name: "thinking", source: "manual", content: null },
];

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; bundles: PromptBundle[] }
  | { kind: "err"; message: string };

type BundleDialogMode =
  | { kind: "closed" }
  | { kind: "add" }
  | { kind: "edit"; bundle: PromptBundle };

export default function Prompt() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<PromptBundle | null>(null);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [traces, setTraces] = useState<PromptTrace[]>([]);
  const [expandedVersionId, setExpandedVersionId] = useState<string | null>(null);
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const [traceDetails, setTraceDetails] = useState<Record<string, PromptTrace>>({});
  const [bundleDialog, setBundleDialog] = useState<BundleDialogMode>({ kind: "closed" });

  const loadBundles = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const { bundles } = await api.listPromptBundles();
      setState({ kind: "ok", bundles });
      setSelected((cur) => {
        if (cur && bundles.some((bundle) => bundle.name === cur)) return cur;
        return bundles[0]?.name ?? null;
      });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message: msg });
    }
  }, []);

  const loadSelected = useCallback(async (name: string) => {
    try {
      const [bundleRes, versionsRes, tracesRes] = await Promise.all([
        api.getPromptBundle(name),
        api.listPromptVersions(name),
        api.listPromptTraces({ bundle_name: name, limit: 20, offset: 0 }),
      ]);
      setDetail(bundleRes.bundle);
      setVersions(versionsRes.versions);
      setTraces(tracesRes.traces);
      setExpandedVersionId(null);
      setExpandedTraceId(null);
      setTraceDetails({});
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail(null);
      setVersions([]);
      setTraces([]);
      setExpandedVersionId(null);
      setExpandedTraceId(null);
      setTraceDetails({});
      setState({ kind: "err", message: msg });
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
      await api.activatePromptBundle({ name, version });
      await refreshAll();
    },
    [refreshAll],
  );

  const inspectTrace = useCallback(async (trace: PromptTrace) => {
    if (expandedTraceId === trace.id && traceDetails[trace.id]) {
      setExpandedTraceId(null);
      return;
    }
    if (traceDetails[trace.id]) {
      setExpandedTraceId(trace.id);
      return;
    }
    try {
      const result = await api.inspectPrompt(trace.id);
      setTraceDetails((cur) => ({ ...cur, [trace.id]: result.trace }));
      setExpandedTraceId(trace.id);
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setTraceDetails((cur) => ({
        ...cur,
        [trace.id]: { ...trace, request: { error: msg } as Record<string, unknown> },
      }));
      setExpandedTraceId(trace.id);
    }
  }, [expandedTraceId, traceDetails]);

  const openEdit = useCallback(() => {
    if (detail) setBundleDialog({ kind: "edit", bundle: detail });
  }, [detail]);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Prompt</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage prompt bundles, versions, and traces.
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
          Unable to load prompt data: {state.message}
        </div>
      )}

      {state.kind === "ok" && (
        <div className="grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)]">
          <section className="rounded-lg border border-border p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Bundles
              </h2>
              <Badge variant="outline">{state.bundles.length}</Badge>
            </div>
            {state.bundles.length === 0 ? (
              <p className="text-sm text-muted-foreground">No prompt bundle yet.</p>
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
                      <button
                        type="button"
                        className="w-full text-left"
                        onClick={() => setSelected(bundle.name)}
                      >
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
                  <Button variant="outline" size="sm" onClick={openEdit} disabled={!detail}>
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

            <div className="grid gap-6">
              <Card title="Versions" subtitle="Expand a version row to inspect its layers.">
                <div className="overflow-hidden rounded-md border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2">version</th>
                        <th className="px-3 py-2">active</th>
                        <th className="px-3 py-2">layers</th>
                        <th className="px-3 py-2 text-right">action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {versions.map((version) => {
                        const expanded = expandedVersionId === version.id;
                        const layers = Array.isArray(version.spec.layers) ? version.spec.layers : [];
                        return (
                          <Fragment key={version.id}>
                            <tr className={"border-t border-border " + (expanded ? "bg-primary/5" : "")}>
                              <td className="px-3 py-2 font-mono">{version.version}</td>
                              <td className="px-3 py-2">{version.is_active ? "yes" : "no"}</td>
                              <td className="px-3 py-2">{layers.length}</td>
                              <td className="px-3 py-2 text-right">
                                <div className="inline-flex items-center gap-2">
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => setExpandedVersionId((cur) => (cur === version.id ? null : version.id))}
                                  >
                                    {expanded ? "Hide layers" : "Show layers"}
                                  </Button>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => void activateBundle(version.bundle_name, version.version)}
                                    disabled={detail == null}
                                  >
                                    Activate
                                  </Button>
                                </div>
                              </td>
                            </tr>
                            {expanded && (
                              <tr className="border-t border-border bg-muted/20">
                                <td colSpan={4} className="px-3 py-3">
                                  {layers.length === 0 ? (
                                    <p className="text-xs text-muted-foreground">(no layers)</p>
                                  ) : (
                                    <pre className="overflow-auto rounded-md border border-border bg-background p-3 text-xs leading-5 whitespace-pre-wrap break-words">
                                      {JSON.stringify(layers, null, 2)}
                                    </pre>
                                  )}
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

              <Card title="Traces" subtitle="Single-line overview. Click inspect to view details.">
                <div className="overflow-hidden rounded-md border border-border">
                  <table className="w-full table-fixed text-sm">
                    <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="w-[24%] px-3 py-2">trace</th>
                        <th className="w-[18%] px-3 py-2">version</th>
                        <th className="w-[22%] px-3 py-2">provider</th>
                        <th className="w-[18%] px-3 py-2">created</th>
                        <th className="w-[18%] px-3 py-2 text-right">action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {traces.map((trace) => {
                        const expanded = expandedTraceId === trace.id;
                        const detailTrace = traceDetails[trace.id] ?? trace;
                        return (
                          <Fragment key={trace.id}>
                            <tr className={"border-t border-border " + (expanded ? "bg-primary/5" : "")}>
                              <td className="px-3 py-2 align-top">
                                <div className="font-mono text-xs">{trace.id.slice(0, 10)}...</div>
                                <div className="mt-1 truncate text-xs text-muted-foreground">
                                  {trace.conversation_id ?? "-"}
                                </div>
                              </td>
                              <td className="px-3 py-2 align-top">
                                <div className="font-mono text-xs">{trace.version}</div>
                                <div className="mt-1 truncate text-xs text-muted-foreground">{trace.model ?? "-"}</div>
                              </td>
                              <td className="px-3 py-2 align-top truncate text-xs text-muted-foreground">
                                {trace.provider_name}
                              </td>
                              <td className="px-3 py-2 align-top truncate text-xs text-muted-foreground">
                                {formatDate(trace.created_at)}
                              </td>
                              <td className="px-3 py-2 text-right">
                                <Button
                                  variant={expanded ? "default" : "outline"}
                                  size="sm"
                                  className="h-7 px-2 text-xs"
                                  onClick={() => void inspectTrace(trace)}
                                >
                                  {expanded ? "Hide" : "Inspect"}
                                </Button>
                              </td>
                            </tr>
                            {expanded && (
                              <tr className="border-t border-border bg-muted/20">
                                <td colSpan={5} className="px-3 py-3">
                                  <div className="mx-auto w-full max-w-2xl">
                                    <TracePanel trace={detailTrace} />
                                  </div>
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
            </div>
          </section>
        </div>
      )}

      {bundleDialog.kind !== "closed" && (
        <BundleDialog
          mode={bundleDialog}
          onClose={() => setBundleDialog({ kind: "closed" })}
          onSaved={async () => {
            setBundleDialog({ kind: "closed" });
            await refreshAll();
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
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm">{value}</div>
    </div>
  );
}

function BundleDialog({
  mode,
  onClose,
  onSaved,
}: {
  mode: BundleDialogMode;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const isEdit = mode.kind === "edit";
  const initial = mode.kind === "edit" ? mode.bundle : null;
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [layersText, setLayersText] = useState(initial ? JSON.stringify(initial.layers, null, 2) : "");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setName(initial?.name ?? "");
    setDescription(initial?.description ?? "");
    setLayersText(initial ? JSON.stringify(initial.layers, null, 2) : "");
    setErr(null);
  }, [initial]);

  const submit = async () => {
    setErr(null);
    const bundleName = name.trim();
    if (!isEdit && bundleName === "") {
      setErr("bundle name cannot be empty");
      return;
    }

    let layers: unknown = undefined;
    if (layersText.trim() !== "") {
      try {
        layers = JSON.parse(layersText);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
        return;
      }
      if (!Array.isArray(layers)) {
        setErr("layers must be a JSON array");
        return;
      }
    }

    setSaving(true);
    try {
      const payload = {
        name: bundleName,
        description: description.trim() === "" ? null : description.trim(),
        ...(layers ? { layers: layers as PromptBundle["layers"] } : {}),
      };
      if (mode.kind === "add") {
        await api.addPromptBundle(payload);
      } else {
        await api.updatePromptBundle(payload);
      }
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? (e as ApiError).message || e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[80vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode.kind === "add" ? "New prompt bundle" : `Edit ${initial?.name ?? ""}`}</DialogTitle>
          <DialogDescription>
            Leave layers empty to keep the default bundle. Paste a JSON array only when you want to override it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Field label="name">
            <Input value={name} onChange={(e) => setName(e.target.value)} disabled={isEdit} />
          </Field>
          <Field label="description">
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
            />
          </Field>
          <Field label="layers JSON">
            {!isEdit && (
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={() => setLayersText(JSON.stringify(DEFAULT_LAYER_TEMPLATE, null, 2))}
                >
                  Fill default template
                </Button>
                <Button variant="ghost" size="sm" type="button" onClick={() => setLayersText("")}>
                  Clear
                </Button>
              </div>
            )}
            <Textarea
              value={layersText}
              onChange={(e) => setLayersText(e.target.value)}
              className="min-h-56 font-mono text-xs"
              placeholder='Leave empty to use default layers, or paste JSON array like [{"name":"base_system","source":"manual","content":"..."}]'
            />
          </Field>
          {err && <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-sm text-destructive">{err}</div>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}

function TracePanel({ trace }: { trace: PromptTrace }) {
  return (
    <div className="space-y-4">
      <div className="space-y-1 border-b border-border/60 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-[10px]">
            trace
          </Badge>
          <span className="break-all font-mono text-xs text-muted-foreground">{trace.id}</span>
        </div>
        <div className="text-xs text-muted-foreground">
          {trace.bundle_name}:{trace.version} · {trace.provider_name}
          {trace.model ? ` · ${trace.model}` : ""} · size {trace.prompt_size} · {formatDate(trace.created_at)}
        </div>
      </div>

      <section className="space-y-2">
        <div className="space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-foreground">Request</h4>
          <p className="text-xs text-muted-foreground">The final request snapshot sent to the model.</p>
        </div>
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted/20 p-4 text-xs leading-6 whitespace-pre-wrap break-words">
          {JSON.stringify(trace.request, null, 2)}
        </pre>
      </section>

      <section className="space-y-2">
        <div className="space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-foreground">Source refs</h4>
          <p className="text-xs text-muted-foreground">The full source_refs payload for this trace.</p>
        </div>
        <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted/20 p-4 text-xs leading-6 whitespace-pre-wrap break-words">
          {JSON.stringify(trace.source_refs, null, 2)}
        </pre>
      </section>
    </div>
  );
}

function formatDate(value: string): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
