import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CollapsibleJson } from "@/components/collapsible-json";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, ApiError, type Tool } from "@/lib/api";

interface ToolsListResponse {
  tools: Tool[];
}

/**
 * Tools 页 —— 内置工具 + 自定义工具管理(0.8.7)。
 *
 * builtin: 只读,可 toggle / 改 options
 * custom: 可增删改,可 toggle / 改 options
 */

type ToolsState =
  | { kind: "loading" }
  | { kind: "ok"; data: ToolsListResponse }
  | { kind: "err"; message: string };

type ProbeState =
  | { kind: "idle" }
  | { kind: "probing" }
  | { kind: "ok"; latency: number }
  | { kind: "fail"; latency: number; code: string; message: string };

export default function Tools() {
  const [state, setState] = useState<ToolsState>({ kind: "loading" });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [pendingEnable, setPendingEnable] = useState<Set<string>>(new Set());
  const [probeStates, setProbeStates] = useState<Record<string, ProbeState>>({});
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = await api.listTools();
      setState({ kind: "ok", data: data as ToolsListResponse });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setState({ kind: "err", message: msg });
    }
  }, []);

  const toggleExpand = useCallback((name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const onToggleEnabled = useCallback(
    async (name: string, enabled: boolean) => {
      setPendingEnable((s) => {
        const n = new Set(s);
        n.add(name);
        return n;
      });
      try {
        await api.setToolState(name, { enabled });
        await load();
      } finally {
        setPendingEnable((s) => {
          const n = new Set(s);
          n.delete(name);
          return n;
        });
      }
    },
    [load],
  );

  const onProbe = useCallback(async (name: string) => {
    setProbeStates((cur) => ({ ...cur, [name]: { kind: "probing" } }));
    try {
      const result = await api.probeTool(name);
      setProbeStates((cur) => ({
        ...cur,
        [name]: result.ok
          ? { kind: "ok", latency: result.latency_ms }
          : {
              kind: "fail",
              latency: result.latency_ms,
              code: result.error?.code ?? "unknown",
              message: result.error?.message ?? "(no detail)",
            },
      }));
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setProbeStates((cur) => ({
        ...cur,
        [name]: { kind: "fail", latency: 0, code: "request_failed", message: msg },
      }));
    }
  }, []);

  const onDelete = useCallback(
    async (name: string) => {
      if (!window.confirm(`Delete custom tool '${name}'?`)) return;
      try {
        await api.deleteTool(name);
        await load();
      } catch (e) {
        const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
        alert(msg);
      }
    },
    [load],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Tools</h1>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void load()}>
            Refresh
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            + New custom tool
          </Button>
        </div>
      </div>

      <ToolsCard
        state={state}
        expanded={expanded}
        pendingEnable={pendingEnable}
        probeStates={probeStates}
        onToggleExpand={toggleExpand}
        onToggleEnabled={onToggleEnabled}
        onProbe={onProbe}
        onDelete={onDelete}
        onOptionsSaved={() => void load()}
      />

      <CreateToolDialog open={showCreate} onClose={() => setShowCreate(false)} onCreated={() => void load()} />
    </section>
  );
}

function ToolsCard({
  state,
  expanded,
  pendingEnable,
  probeStates,
  onToggleExpand,
  onToggleEnabled,
  onProbe,
  onDelete,
  onOptionsSaved,
}: {
  state: ToolsState;
  expanded: Set<string>;
  pendingEnable: Set<string>;
  probeStates: Record<string, ProbeState>;
  onToggleExpand: (name: string) => void;
  onToggleEnabled: (name: string, enabled: boolean) => void | Promise<void>;
  onProbe: (name: string) => void | Promise<void>;
  onDelete: (name: string) => void | Promise<void>;
  onOptionsSaved: () => void;
}) {
  if (state.kind === "loading") {
    return (
      <div className="max-w-4xl rounded-lg border border-border p-4 text-sm text-muted-foreground">
        Loading tools…
      </div>
    );
  }
  if (state.kind === "err") {
    return (
      <div className="max-w-4xl rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        无法读取工具列表:{state.message}
      </div>
    );
  }
  const { data } = state;

  return (
    <div className="max-w-4xl rounded-lg border border-border p-4">
      <div className="mb-2 text-xs text-muted-foreground">
        builtin 只读,custom 可增删改。toggle 即时生效(server rebuild Agent),
        展开行可编辑 options 与查看 anthropic tool schema。
      </div>
      <ul className="mb-4 divide-y divide-border rounded-md border border-border">
        {data.tools.map((tool: Tool) => (
          <ToolRow
            key={tool.name}
            tool={tool}
            isExpanded={expanded.has(tool.name)}
            isPending={pendingEnable.has(tool.name)}
            probeState={probeStates[tool.name] ?? { kind: "idle" }}
            onToggleExpand={() => onToggleExpand(tool.name)}
            onToggleEnabled={(v) => void onToggleEnabled(tool.name, v)}
            onProbe={() => void onProbe(tool.name)}
            onDelete={() => void onDelete(tool.name)}
            onOptionsSaved={onOptionsSaved}
          />
        ))}
      </ul>

      <div className="text-xs text-muted-foreground">
        registered types:{" "}
        {Array.from(new Set(data.tools.map((tool: Tool) => tool.type))).map((t: string, i: number) => (
          <span key={t}>
            {i > 0 && ", "}
            <code className="font-mono">{t}</code>
          </span>
        ))}
      </div>
    </div>
  );
}

function ToolRow({
  tool,
  isExpanded,
  isPending,
  probeState,
  onToggleExpand,
  onToggleEnabled,
  onProbe,
  onDelete,
  onOptionsSaved,
}: {
  tool: Tool;
  isExpanded: boolean;
  isPending: boolean;
  probeState: ProbeState;
  onToggleExpand: () => void;
  onToggleEnabled: (v: boolean) => void;
  onProbe: () => void;
  onDelete: () => void;
  onOptionsSaved: () => void;
}) {
  const isCustom = tool.source === "custom";
  return (
    <li>
      <div className="px-3 py-2 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onToggleExpand}
            className="flex items-center gap-2 text-left hover:text-foreground"
            aria-expanded={isExpanded}
          >
            <span className="text-xs text-muted-foreground select-none">
              {isExpanded ? "▾" : "▸"}
            </span>
            <code className="font-mono">{tool.name}</code>
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              {tool.type}
            </Badge>
            <Badge
              variant="outline"
              className={`h-5 px-1.5 text-[10px] ${isCustom ? "text-blue-600 border-blue-300/50" : "text-muted-foreground"}`}
            >
              {tool.source ?? "builtin"}
            </Badge>
            {tool.schema_ === null && (
              <Badge
                variant="outline"
                className="h-5 px-1.5 text-[10px] text-amber-600 border-amber-300/50"
                title="options 不合法,无法生成 schema;启用后 Agent rebuild 会被该 tool 阻塞"
              >
                schema invalid
              </Badge>
            )}
          </button>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-3 text-xs"
              onClick={onProbe}
            >
              {probeState.kind === "probing"
                ? "Probing..."
                : probeState.kind === "ok"
                  ? `Probe ${probeState.latency}ms`
                  : probeState.kind === "fail"
                    ? "Probe failed"
                    : "Probe"}
            </Button>
            {isCustom && (
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-3 text-xs text-destructive hover:bg-destructive/10"
                onClick={onDelete}
              >
                Delete
              </Button>
            )}
            <Button
              variant={tool.enabled ? "default" : "outline"}
              size="sm"
              className="h-7 px-3 text-xs"
              onClick={() => onToggleEnabled(!tool.enabled)}
              disabled={isPending}
              aria-pressed={tool.enabled}
            >
              {isPending ? "…" : tool.enabled ? "ON" : "off"}
            </Button>
          </div>
        </div>
        {tool.description && (
          <div className="mt-1 text-xs text-muted-foreground pl-5">
            {tool.description}
          </div>
        )}
      </div>
      {isExpanded && (
        <>
          <OptionsEditor tool={tool} onSaved={onOptionsSaved} />
          <SchemaBlock tool={tool} />
          {probeState.kind === "ok" && (
            <div className="border-t border-border bg-muted/10 px-3 py-2 text-xs text-emerald-700">
              Probe OK in {probeState.latency}ms
            </div>
          )}
          {probeState.kind === "fail" && (
            <div className="border-t border-border bg-muted/10 px-3 py-2 text-xs text-destructive">
              Probe failed in {probeState.latency}ms [{probeState.code}] {probeState.message}
            </div>
          )}
        </>
      )}
    </li>
  );
}

// ---------- OptionsEditor(KV 编辑器,整体替换 options)----------

interface KvRow {
  key: string;
  text: string;
}

function optionsToRows(opts: Record<string, unknown>): KvRow[] {
  return Object.keys(opts)
    .sort()
    .map((k) => ({ key: k, text: stringifyForEdit(opts[k]) }));
}

function stringifyForEdit(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null) return "null";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function parseForSave(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

function rowsToOptions(rows: KvRow[]): {
  options: Record<string, unknown>;
  err: string | null;
} {
  const seen = new Set<string>();
  const options: Record<string, unknown> = {};
  for (const r of rows) {
    const k = r.key.trim();
    if (k === "") continue;
    if (seen.has(k)) {
      return { options: {}, err: `key 重复:${k}` };
    }
    seen.add(k);
    options[k] = parseForSave(r.text);
  }
  return { options, err: null };
}

function OptionsEditor({ tool, onSaved }: { tool: Tool; onSaved: () => void }) {
  const [rows, setRows] = useState<KvRow[]>(() => optionsToRows(tool.options));
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setRows(optionsToRows(tool.options));
    setErr(null);
  }, [tool]);

  const updateRow = (i: number, patch: Partial<KvRow>) => {
    setRows((cur) => {
      const copy = cur.slice();
      copy[i] = { ...copy[i], ...patch };
      return copy;
    });
  };

  const removeRow = (i: number) => {
    setRows((cur) => cur.filter((_, idx) => idx !== i));
  };

  const addRow = () => {
    setRows((cur) => [...cur, { key: "", text: "" }]);
  };

  const reset = () => {
    setRows(optionsToRows(tool.options));
    setErr(null);
  };

  const save = async () => {
    setErr(null);
    const { options, err: parseErr } = rowsToOptions(rows);
    if (parseErr) {
      setErr(parseErr);
      return;
    }
    setSubmitting(true);
    try {
      await api.updateTool(tool.name, { options });
      onSaved();
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="border-t border-border bg-muted/20 px-3 py-3 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          options
        </span>
        <span className="text-[11px] text-muted-foreground">
          整体替换(非 merge);value 试 JSON 解析
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="mb-2 text-muted-foreground">(no options)</p>
      ) : (
        <ul className="mb-2 space-y-1">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-2">
              <Input
                value={r.key}
                onChange={(e) => updateRow(i, { key: e.target.value })}
                placeholder="key"
                className="h-7 w-48 font-mono text-xs"
              />
              <Input
                value={r.text}
                onChange={(e) => updateRow(i, { text: e.target.value })}
                placeholder="value"
                className="h-7 flex-1 font-mono text-xs"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-7 w-7 p-0 text-destructive hover:bg-destructive/10"
                onClick={() => removeRow(i)}
                aria-label="remove row"
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      )}

      {err && (
        <div className="mb-2 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive">
          {err}
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={addRow}
          disabled={submitting}
        >
          + Add row
        </Button>
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={reset}
            disabled={submitting}
          >
            Reset
          </Button>
          <Button
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => void save()}
            disabled={submitting}
          >
            {submitting ? "Saving…" : "Save options"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------- SchemaBlock(只读;LLM 看到的 anthropic tool 定义)----------

function SchemaBlock({ tool }: { tool: Tool }) {
  return (
    <div className="border-t border-border bg-muted/10 px-3 py-2 text-xs">
      <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
        anthropic schema
      </div>
      {tool.schema_ === null ? (
        <p className="text-amber-700 dark:text-amber-300">
          options 不合法 → 当前没有 schema;修正 options 后 Save。
        </p>
      ) : (
        <CollapsibleJson title="Anthropic schema JSON" value={tool.schema_} defaultExpanded maxHeightClassName="max-h-48" />
      )}
    </div>
  );
}

// ---------- CreateToolDialog ----------

function CreateToolDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [customType, setCustomType] = useState<"http_custom" | "shell_custom">("http_custom");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [optionsJson, setOptionsJson] = useState("{}");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reset = () => {
    setCustomType("http_custom");
    setName("");
    setDescription("");
    setOptionsJson("{}");
    setErr(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleCreate = async () => {
    setErr(null);
    if (!name.trim()) {
      setErr("name is required");
      return;
    }
    let options: Record<string, unknown>;
    try {
      options = JSON.parse(optionsJson);
    } catch {
      setErr("options must be valid JSON");
      return;
    }
    if (typeof options !== "object" || options === null) {
      setErr("options must be an object");
      return;
    }
    setSubmitting(true);
    try {
      await api.createTool({
        name: name.trim(),
        custom_type: customType,
        description: description.trim(),
        options,
      });
      onCreated();
      handleClose();
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const httpExample = JSON.stringify(
    {
      method: "GET",
      url_template: "https://api.example.com/items/${id}",
      headers: { Authorization: "Bearer ${token}" },
      timeout_s: 10,
      max_bytes: 102400,
    },
    null,
    2,
  );

  const shellExample = JSON.stringify(
    {
      command_template: "echo ${message}",
      workdir: ".",
      timeout_s: 30,
    },
    null,
    2,
  );

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New custom tool</DialogTitle>
          <DialogDescription>Create a custom HTTP or shell tool.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label className="text-xs">Type</Label>
            <div className="flex gap-2 mt-1">
              <Button
                variant={customType === "http_custom" ? "default" : "outline"}
                size="sm"
                className="text-xs"
                onClick={() => setCustomType("http_custom")}
              >
                http_custom
              </Button>
              <Button
                variant={customType === "shell_custom" ? "default" : "outline"}
                size="sm"
                className="text-xs"
                onClick={() => setCustomType("shell_custom")}
              >
                shell_custom
              </Button>
            </div>
          </div>

          <div>
            <Label className="text-xs">Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my_api"
              className="h-8 text-xs mt-1"
            />
          </div>

          <div>
            <Label className="text-xs">Description</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this tool does"
              className="h-8 text-xs mt-1"
            />
          </div>

          <div>
            <Label className="text-xs">Options (JSON)</Label>
            <textarea
              value={optionsJson}
              onChange={(e) => setOptionsJson(e.target.value)}
              className="mt-1 w-full min-h-[120px] rounded-md border border-input bg-background px-3 py-2 text-xs font-mono"
              placeholder={customType === "http_custom" ? httpExample : shellExample}
            />
            <p className="text-[10px] text-muted-foreground mt-1">
              Use {'${var}'} for template variables. Example above.
            </p>
          </div>

          {err && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
              {err}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void handleCreate()} disabled={submitting}>
            {submitting ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
