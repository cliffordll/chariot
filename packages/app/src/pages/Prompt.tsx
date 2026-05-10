import { useCallback, useEffect, useMemo, useState } from "react";

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

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; bundles: PromptBundle[] }
  | { kind: "err"; message: string };

type BundleDialogMode =
  | { kind: "closed" }
  | { kind: "add" }
  | { kind: "edit"; bundle: PromptBundle };

type TraceDialogMode =
  | { kind: "closed" }
  | { kind: "open"; trace: PromptTrace; detail?: PromptTrace };

export default function Prompt() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [traces, setTraces] = useState<PromptTrace[]>([]);
  const [detail, setDetail] = useState<PromptBundle | null>(null);
  const [bundleDialog, setBundleDialog] = useState<BundleDialogMode>({ kind: "closed" });
  const [traceDialog, setTraceDialog] = useState<TraceDialogMode>({ kind: "closed" });

  const loadBundles = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const { bundles } = await api.listPromptBundles();
      setState({ kind: "ok", bundles });
      setSelected((cur) => {
        if (cur && bundles.some((b) => b.name === cur)) return cur;
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
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setDetail(null);
      setVersions([]);
      setTraces([]);
      setState({ kind: "err", message: msg });
    }
  }, []);

  useEffect(() => {
    void loadBundles();
  }, [loadBundles]);

  useEffect(() => {
    if (selected) {
      void loadSelected(selected);
    }
  }, [selected, loadSelected]);

  const selectedBundle = useMemo(
    () => (state.kind === "ok" ? state.bundles.find((bundle) => bundle.name === selected) ?? null : null),
    [state, selected],
  );

  const refreshAll = useCallback(async () => {
    await loadBundles();
    if (selected) {
      await loadSelected(selected);
    }
  }, [loadBundles, loadSelected, selected]);

  const activateBundle = useCallback(
    async (name: string, version?: string) => {
      await api.activatePromptBundle({ name, version });
      await refreshAll();
    },
    [refreshAll],
  );

  const openEdit = useCallback(() => {
    if (detail) setBundleDialog({ kind: "edit", bundle: detail });
  }, [detail]);

  const inspectTrace = useCallback(async (trace: PromptTrace) => {
    setTraceDialog({ kind: "open", trace });
    try {
      const result = await api.inspectPrompt(trace.id);
      setTraceDialog({ kind: "open", trace, detail: result.trace });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setTraceDialog({
        kind: "open",
        trace,
        detail: { ...trace, request: { error: msg } as Record<string, unknown> },
      });
    }
  }, []);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Prompt</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            管理 prompt bundle、版本和 trace。新建和更新支持可重复的 `layers`。
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
          无法读取 prompt 数据：{state.message}
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
              <p className="text-sm text-muted-foreground">还没有 prompt bundle。</p>
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
                          {bundle.description || "没有说明"}
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

            <div className="grid gap-6 lg:grid-cols-2">
              <Card title="Versions" subtitle="切换 bundle 的当前版本">
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
                      {versions.map((version) => (
                        <tr key={version.id} className="border-t border-border">
                          <td className="px-3 py-2 font-mono">{version.version}</td>
                          <td className="px-3 py-2">{version.is_active ? "yes" : "no"}</td>
                          <td className="px-3 py-2">
                            {Array.isArray(version.spec.layers) ? version.spec.layers.length : "-"}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => void activateBundle(version.bundle_name, version.version)}
                              disabled={detail == null}
                            >
                              Activate
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card title="Traces" subtitle="查看最近 20 条 prompt trace">
                <div className="overflow-hidden rounded-md border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2">trace</th>
                        <th className="px-3 py-2">version</th>
                        <th className="px-3 py-2">conversation</th>
                        <th className="px-3 py-2 text-right">action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {traces.map((trace) => (
                        <tr key={trace.id} className="border-t border-border">
                          <td className="px-3 py-2 font-mono">{trace.id.slice(0, 10)}...</td>
                          <td className="px-3 py-2 font-mono">{trace.version}</td>
                          <td className="px-3 py-2 font-mono">{trace.conversation_id ?? "-"}</td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={() => void inspectTrace(trace)}
                            >
                              Inspect
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>

            <Card title="Layers" subtitle="bundle 当前层级定义">
              <div className="overflow-hidden rounded-md border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">layer</th>
                      <th className="px-3 py-2">source</th>
                      <th className="px-3 py-2">content</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(detail?.layers ?? []).map((layer) => (
                      <tr key={layer.name} className="border-t border-border align-top">
                        <td className="px-3 py-2 font-mono">{layer.name}</td>
                        <td className="px-3 py-2 text-muted-foreground">{layer.source}</td>
                        <td className="px-3 py-2">
                          <pre className="whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">
                            {formatLayerContent(layer.content)}
                          </pre>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
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

      {traceDialog.kind === "open" && (
        <TraceDialog
          trace={traceDialog.detail ?? traceDialog.trace}
          onClose={() => setTraceDialog({ kind: "closed" })}
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
  const [layersText, setLayersText] = useState(
    initial ? JSON.stringify(initial.layers, null, 2) : "",
  );
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
      setErr("bundle 名称不能为空");
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
        setErr("layers 必须是 JSON 数组");
        return;
      }
    }
    setSaving(true);
    try {
      if (mode.kind === "add") {
        await api.addPromptBundle({
          name: bundleName,
          description: description.trim() === "" ? null : description.trim(),
          ...(layers ? { layers: layers as PromptBundle["layers"] } : {}),
        });
      } else {
        await api.updatePromptBundle({
          name: bundleName,
          description: description.trim() === "" ? null : description.trim(),
          ...(layers ? { layers: layers as PromptBundle["layers"] } : {}),
        });
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
            layers 为空时，新增会使用默认层级；编辑时空白表示不修改 layers。
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
              placeholder="可选说明"
            />
          </Field>
          <Field label="layers JSON">
            <Textarea
              value={layersText}
              onChange={(e) => setLayersText(e.target.value)}
              className="min-h-56 font-mono text-xs"
              placeholder='[{"name":"base_system","source":"user","content":"..."}]'
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

function TraceDialog({
  trace,
  onClose,
}: {
  trace: PromptTrace;
  onClose: () => void;
}) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Prompt trace</DialogTitle>
          <DialogDescription>
            {trace.bundle_name}:{trace.version} · {formatDate(trace.created_at)}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 md:grid-cols-2">
          <Info label="trace" value={trace.id} />
          <Info label="conversation" value={trace.conversation_id ?? "-"} />
          <Info label="provider" value={trace.provider_name} />
          <Info label="model" value={trace.model ?? "-"} />
        </div>

        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            request
          </h4>
          <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted/20 p-3 text-xs">
            {JSON.stringify(trace.request, null, 2)}
          </pre>
        </section>

        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            source refs
          </h4>
          <pre className="max-h-72 overflow-auto rounded-md border border-border bg-muted/20 p-3 text-xs">
            {JSON.stringify(trace.source_refs, null, 2)}
          </pre>
        </section>

        <DialogFooter>
          <Button onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function formatLayerContent(content: unknown): string {
  if (content === null || content === undefined) return "-";
  if (typeof content === "string") return content;
  return JSON.stringify(content, null, 2);
}

function formatDate(value: string): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
