import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError, type Tool } from "@/lib/api";

interface ToolsListResponse {
  tools: Tool[];
}

/**
 * Tools 页 —— 内置工具配置(0.4.0)。
 *
 * 4 条 fixture(name + type 不可改,数量固定):read_file / list_dir / shell_exec / http_get。
 * 可改:enabled(toggle)、options(KV 编辑器,整体替换)。改动后 server 立即 rebuild
 * Agent.tools,LLM 下一轮可见。
 *
 * UI 模式照抄 Models 页:行内 toggle + 展开行编辑 options + schema 预览。
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
        await api.updateTool(name, { enabled });
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

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Tools</h1>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      <ToolsCard
        state={state}
        expanded={expanded}
        pendingEnable={pendingEnable}
        probeStates={probeStates}
        onToggleExpand={toggleExpand}
        onToggleEnabled={onToggleEnabled}
        onProbe={onProbe}
        onOptionsSaved={() => void load()}
      />
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
  onOptionsSaved,
}: {
  state: ToolsState;
  expanded: Set<string>;
  pendingEnable: Set<string>;
  probeStates: Record<string, ProbeState>;
  onToggleExpand: (name: string) => void;
  onToggleEnabled: (name: string, enabled: boolean) => void | Promise<void>;
  onProbe: (name: string) => void | Promise<void>;
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
        4 条 fixture · name + type 不可改。toggle 即时生效(server rebuild Agent),
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
  onOptionsSaved,
}: {
  tool: Tool;
  isExpanded: boolean;
  isPending: boolean;
  probeState: ProbeState;
  onToggleExpand: () => void;
  onToggleEnabled: (v: boolean) => void;
  onProbe: () => void;
  onOptionsSaved: () => void;
}) {
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
        <pre className="max-h-48 overflow-auto rounded bg-background p-2 font-mono text-[11px] leading-snug">
          {JSON.stringify(tool.schema_, null, 2)}
        </pre>
      )}
    </div>
  );
}
