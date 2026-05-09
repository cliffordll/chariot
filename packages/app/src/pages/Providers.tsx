import { useCallback, useEffect, useState } from "react";

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
  api,
  type ApiError,
  type ProviderEntry,
  type ProvidersListResponse,
} from "@/lib/api";

/**
 * Providers 页 —— Provider 管理(0.6.0+ Model 概念整体 rename Provider)。
 *
 * 能力:
 * - 列出所有 entries:行内拆开展示 model / api_key(脱敏) / base_url 主键
 *   注:`options.model` 是 Anthropic API 的 LLM model id 字段(如 claude-sonnet-4-6),
 *   跟 Provider 概念不是一回事 —— Provider 是 chariot 内部的路由 entry,
 *   model 是上游 LLM 真实 id
 * - 展开行 → ParamsEditor:KV 形式编辑 sampling 默认参数,Save 写 DB
 * - 新建 / 编辑 / 复制 / 删除 entry(走 sidecar `add_provider` / `edit_provider` /
 *   `delete_provider` RPC method)
 * - Test 按钮跑探针(`probe_provider`)
 *
 * Chat 页负责选哪个 entry(`ChatRequest.provider_name` 路由)。
 */

interface FieldSchema {
  key: string;
  label: string;
  required?: boolean;
  placeholder?: string;
  isSecret?: boolean;
}

const TYPE_SCHEMAS: Record<string, FieldSchema[]> = {
  mock: [],
  anthropic: [
    {
      key: "model",
      label: "model",
      required: true,
      placeholder: "claude-opus-4-5",
    },
    {
      key: "api_key",
      label: "api_key(留空则用 env)",
      placeholder: "sk-ant-...",
      isSecret: true,
    },
    {
      key: "api_key_env",
      label: "api_key_env",
      placeholder: "ANTHROPIC_API_KEY(默认)",
    },
    {
      key: "base_url",
      label: "base_url",
      placeholder: "https://api.anthropic.com(默认)",
    },
  ],
};

/**
 * Add 模式下的快速预设。点击 chip → 自动填 name + options.model。
 * 用户仍需自己填 api_key(或留空用 env)。其它 type 暂不预设。
 */
interface ProviderTemplate {
  id: string;
  label: string;
  type: string;
  options: Record<string, string>;
}

const TEMPLATES: ProviderTemplate[] = [
  {
    id: "claude-opus-4-5",
    label: "Claude Opus 4.5",
    type: "anthropic",
    options: { model: "claude-opus-4-5" },
  },
  {
    id: "claude-sonnet-4-6",
    label: "Claude Sonnet 4.6",
    type: "anthropic",
    options: { model: "claude-sonnet-4-6" },
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    type: "anthropic",
    options: { model: "claude-haiku-4-5" },
  },
];

type ProvidersState =
  | { kind: "loading" }
  | { kind: "ok"; data: ProvidersListResponse }
  | { kind: "err"; message: string };

type ProbeState =
  | { kind: "idle" }
  | { kind: "probing" }
  | { kind: "ok"; latency: number }
  | { kind: "fail"; latency: number; code: string; message: string };

type DialogMode =
  | { kind: "closed" }
  | { kind: "add" }
  | { kind: "edit"; source: ProviderEntry }
  | { kind: "duplicate"; source: ProviderEntry };

type DeleteState = { open: false } | { open: true; name: string };

export default function Providers() {
  const [providersState, setProvidersState] = useState<ProvidersState>({ kind: "loading" });
  const [probeStates, setProbeStates] = useState<Record<string, ProbeState>>({});
  const [dialog, setDialog] = useState<DialogMode>({ kind: "closed" });
  const [del, setDel] = useState<DeleteState>({ open: false });
  /** 哪些行处于展开状态(name set);点击行头切换。 */
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setProvidersState({ kind: "loading" });
    try {
      const data = await api.listModels();
      setProvidersState({ kind: "ok", data });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setProvidersState({ kind: "err", message: msg });
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

  const runProbe = useCallback(async (name: string) => {
    setProbeStates((s) => ({ ...s, [name]: { kind: "probing" } }));
    try {
      const r = await api.probeModel(name);
      setProbeStates((s) => ({
        ...s,
        [name]: r.ok
          ? { kind: "ok", latency: r.latency_ms }
          : {
              kind: "fail",
              latency: r.latency_ms,
              code: r.error?.code ?? "unknown",
              message: r.error?.message ?? "(no detail)",
            },
      }));
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setProbeStates((s) => ({
        ...s,
        [name]: { kind: "fail", latency: 0, code: "request_failed", message: msg },
      }));
    }
  }, []);

  const findEntry = useCallback(
    (name: string): ProviderEntry | undefined => {
      if (providersState.kind !== "ok") return undefined;
      return providersState.data.entries.find((e) => e.name === name);
    },
    [providersState],
  );

  const openEdit = useCallback(
    (name: string) => {
      const source = findEntry(name);
      if (source) setDialog({ kind: "edit", source });
    },
    [findEntry],
  );

  const openDuplicate = useCallback(
    (name: string) => {
      const source = findEntry(name);
      if (source) setDialog({ kind: "duplicate", source });
    },
    [findEntry],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Providers</h1>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setDialog({ kind: "add" })}>
            + Add
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            Refresh
          </Button>
        </div>
      </div>

      <ProvidersCard
        providersState={providersState}
        probeStates={probeStates}
        expanded={expanded}
        onToggleExpand={toggleExpand}
        onProbe={runProbe}
        onEdit={openEdit}
        onDuplicate={openDuplicate}
        onDelete={(name) => setDel({ open: true, name })}
        onParamsSaved={() => void load()}
      />

      {dialog.kind !== "closed" && (
        <AddEditDialog
          mode={dialog}
          knownTypes={providersState.kind === "ok" ? providersState.data.types : []}
          onClose={() => setDialog({ kind: "closed" })}
          onSuccess={() => void load()}
        />
      )}

      {del.open && (
        <DeleteDialog
          name={del.name}
          onClose={() => setDel({ open: false })}
          onSuccess={() => {
            setDel({ open: false });
            void load();
          }}
        />
      )}
    </section>
  );
}

// ---------- 列表卡片 ----------

function ProvidersCard({
  providersState,
  probeStates,
  expanded,
  onToggleExpand,
  onProbe,
  onEdit,
  onDuplicate,
  onDelete,
  onParamsSaved,
}: {
  providersState: ProvidersState;
  probeStates: Record<string, ProbeState>;
  expanded: Set<string>;
  onToggleExpand: (name: string) => void;
  onProbe: (name: string) => void;
  onEdit: (name: string) => void;
  onDuplicate: (name: string) => void;
  onDelete: (name: string) => void;
  onParamsSaved: () => void;
}) {
  if (providersState.kind === "loading") {
    return (
      <div className="max-w-4xl rounded-lg border border-border p-4 text-sm text-muted-foreground">
        Loading providers…
      </div>
    );
  }
  if (providersState.kind === "err") {
    return (
      <div className="max-w-4xl rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        无法读取 provider 列表:{providersState.message}
      </div>
    );
  }
  const { data } = providersState;

  return (
    <div className="max-w-4xl rounded-lg border border-border p-4">
      {data.entries.length === 0 ? (
        <p className="mb-4 text-xs text-muted-foreground">
          DB 里没有 entry。点击右上角 <code className="font-mono">+ Add</code> 新建一条。
        </p>
      ) : (
        <>
          <div className="mb-2 text-xs text-muted-foreground">
            Test 会真打上游一次(~1 token;mock 零费用)。展开行可编辑 params(Chat
            切到该 entry 时用作 sampling 默认值)—— 一行一对 KV,数字 / true / false / null
            直写,字符串免引号。
          </div>
          <ul className="mb-4 divide-y divide-border rounded-md border border-border">
            {data.entries.map((entry) => (
              <ProviderRow
                key={entry.name}
                entry={entry}
                state={probeStates[entry.name] ?? { kind: "idle" }}
                isExpanded={expanded.has(entry.name)}
                onToggleExpand={() => onToggleExpand(entry.name)}
                onProbe={() => onProbe(entry.name)}
                onEdit={() => onEdit(entry.name)}
                onDuplicate={() => onDuplicate(entry.name)}
                onDelete={() => onDelete(entry.name)}
                onParamsSaved={onParamsSaved}
              />
            ))}
          </ul>
        </>
      )}

      <div className="text-xs text-muted-foreground">
        registered types:{" "}
        {data.types.map((t, i) => (
          <span key={t}>
            {i > 0 && ", "}
            <code className="font-mono">{t}</code>
          </span>
        ))}
      </div>
    </div>
  );
}

function ProviderRow({
  entry,
  state,
  isExpanded,
  onToggleExpand,
  onProbe,
  onEdit,
  onDuplicate,
  onDelete,
  onParamsSaved,
}: {
  entry: ProviderEntry;
  state: ProbeState;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onProbe: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onParamsSaved: () => void;
}) {
  return (
    <li>
      <div className="px-3 py-2 text-sm">
        {/* 行头(只剩 name + type + 操作按钮;options 主键放展开区)*/}
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
            <code className="font-mono">{entry.name}</code>
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              {entry.type}
            </Badge>
          </button>
          <ProbeStatus state={state} />
          <div className="ml-auto flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={onProbe}
              disabled={state.kind === "probing"}
            >
              {state.kind === "probing" ? "Testing…" : "Test"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={onDuplicate}
            >
              Dup
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={onEdit}
            >
              Edit
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs text-destructive hover:bg-destructive/10"
              onClick={onDelete}
            >
              Del
            </Button>
          </div>
        </div>
      </div>
      {isExpanded && (
        <>
          <OptionsBlock entry={entry} />
          <ParamsEditor entry={entry} onSaved={onParamsSaved} />
        </>
      )}
    </li>
  );
}

/** 展开区 · options 主键只读展示(model / api_key 脱敏 / base_url)。 */
function OptionsBlock({ entry }: { entry: ProviderEntry }) {
  const o = entry.options;
  const model = typeof o.model === "string" ? o.model : null;
  const apiKey = typeof o.api_key === "string" ? o.api_key : null;
  const apiKeyEnv = typeof o.api_key_env === "string" ? o.api_key_env : null;
  const baseUrl = typeof o.base_url === "string" ? o.base_url : null;

  const hasAny = model || apiKey || apiKeyEnv || baseUrl;

  return (
    <div className="border-t border-border bg-muted/10 px-3 py-2 text-xs">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">options</span>
        <span className="text-[11px] text-muted-foreground">改用顶部 [Edit] 按钮</span>
      </div>
      {hasAny ? (
        <div className="grid grid-cols-1 gap-x-6 gap-y-0.5 sm:grid-cols-[auto_1fr]">
          {model !== null && (
            <SummaryKv label="model" value={<code className="font-mono">{model}</code>} />
          )}
          {apiKey !== null && (
            <SummaryKv
              label="api_key"
              value={<code className="font-mono">{maskApiKey(apiKey)}</code>}
            />
          )}
          {apiKey === null && apiKeyEnv !== null && (
            <SummaryKv
              label="api_key_env"
              value={<code className="font-mono">{apiKeyEnv}</code>}
            />
          )}
          {baseUrl !== null && (
            <SummaryKv
              label="base_url"
              value={<code className="font-mono break-all">{baseUrl}</code>}
            />
          )}
        </div>
      ) : (
        <span className="text-muted-foreground">(empty — mock 等不需 options 的 type)</span>
      )}
    </div>
  );
}

function SummaryKv({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <>
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </>
  );
}

function maskApiKey(value: string): string {
  if (value.length === 0) return "";
  if (value.length <= 12) return "***";
  return value.slice(0, 7) + "***...***" + value.slice(-4);
}

function ProbeStatus({ state }: { state: ProbeState }) {
  if (state.kind === "idle") return null;
  if (state.kind === "probing") {
    return <span className="text-xs text-muted-foreground">…</span>;
  }
  if (state.kind === "ok") {
    return (
      <span className="text-xs text-emerald-600 dark:text-emerald-400">
        ✓ {state.latency} ms
      </span>
    );
  }
  return (
    <span
      className="max-w-[14rem] truncate text-xs text-destructive"
      title={`[${state.code}] ${state.message}`}
    >
      ✗ {state.latency} ms · {state.code}
    </span>
  );
}

// ---------- ParamsEditor ----------

/** Anthropic Messages 协议常用 sampling 字段;点击 chip 一键加一行该 key。 */
const PARAM_PRESETS: { key: string; hint: string }[] = [
  { key: "temperature", hint: "0-1" },
  { key: "top_p", hint: "0-1" },
  { key: "top_k", hint: "int ≥ 1" },
  { key: "max_tokens", hint: "int ≥ 1" },
  { key: "stop_sequences", hint: '["...","..."]' },
];

interface ParamRow {
  key: string;
  /** value 在编辑器里始终以字符串形式持有;Save 时按 JSON 解析(失败回退字符串)。*/
  text: string;
}

/** 把 entry.params 转成可编辑行;keys 排序保证渲染稳定。 */
function paramsToRows(params: Record<string, unknown>): ParamRow[] {
  return Object.keys(params)
    .sort()
    .map((k) => ({ key: k, text: stringifyForEdit(params[k]) }));
}

function stringifyForEdit(v: unknown): string {
  if (typeof v === "string") return v; // 字符串不加引号(免去用户写 "")
  if (v === null) return "null";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/** 把编辑器行的字符串值转回 JSON value。
 *  - 优先 JSON.parse(text),允许 number / bool / null / object / array
 *  - 解析失败 → 视为普通字符串(免引号体验) */
function parseForSave(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

function rowsToParams(rows: ParamRow[]): { params: Record<string, unknown>; err: string | null } {
  const seen = new Set<string>();
  const params: Record<string, unknown> = {};
  for (const r of rows) {
    const k = r.key.trim();
    if (k === "") continue; // 跳过空 key 行(允许悬空 +Add)
    if (seen.has(k)) {
      return { params: {}, err: `key 重复:${k}` };
    }
    seen.add(k);
    params[k] = parseForSave(r.text);
  }
  return { params, err: null };
}

function ParamsEditor({
  entry,
  onSaved,
}: {
  entry: ProviderEntry;
  onSaved: () => void;
}) {
  const [rows, setRows] = useState<ParamRow[]>(() => paramsToRows(entry.params));
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 当 entry.params 来源变化(刷新后)→ 重置编辑器(避免本地脏 state 跟 server 不一致)
  useEffect(() => {
    setRows(paramsToRows(entry.params));
    setErr(null);
  }, [entry]);

  const updateRow = (i: number, patch: Partial<ParamRow>) => {
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
    setRows(paramsToRows(entry.params));
    setErr(null);
  };

  const save = async () => {
    setErr(null);
    const { params, err: parseErr } = rowsToParams(rows);
    if (parseErr) {
      setErr(parseErr);
      return;
    }
    setSubmitting(true);
    try {
      await api.updateModel(entry.name, { params });
      onSaved();
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const addPreset = (key: string) => {
    setRows((cur) => {
      if (cur.some((r) => r.key === key)) return cur; // 已存在 → noop
      return [...cur, { key, text: "" }];
    });
  };

  const presentKeys = new Set(rows.map((r) => r.key));

  return (
    <div className="border-t border-border bg-muted/20 px-3 py-3 text-xs">
      <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
        params
      </div>

      {/* 预设 chips:点击一键加常用 sampling 字段;已存在的 key 灰掉。 */}
      <div className="mb-2 flex flex-wrap items-center gap-1">
        <span className="mr-1 text-[11px] text-muted-foreground">presets:</span>
        {PARAM_PRESETS.map((p) => (
          <Button
            key={p.key}
            variant="outline"
            size="sm"
            className="h-6 px-2 text-[11px]"
            onClick={() => addPreset(p.key)}
            disabled={presentKeys.has(p.key) || submitting}
            title={p.hint}
          >
            + {p.key}
          </Button>
        ))}
      </div>

      {rows.length === 0 ? null : (
        <ul className="mb-2 space-y-1">
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-2">
              <Input
                value={r.key}
                onChange={(e) => updateRow(i, { key: e.target.value })}
                placeholder="key (e.g. temperature)"
                className="h-7 w-48 font-mono text-xs"
              />
              <Input
                value={r.text}
                onChange={(e) => updateRow(i, { text: e.target.value })}
                placeholder="value (e.g. 0.7)"
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
            {submitting ? "Saving…" : "Save params"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------- AddEditDialog ----------

function AddEditDialog({
  mode,
  knownTypes,
  onClose,
  onSuccess,
}: {
  mode: Exclude<DialogMode, { kind: "closed" }>;
  knownTypes: string[];
  onClose: () => void;
  onSuccess: () => void;
}) {
  const isEdit = mode.kind === "edit";
  const isDup = mode.kind === "duplicate";
  const initial = mode.kind === "add" ? null : mode.source;

  const [name, setName] = useState(
    isEdit ? initial!.name : isDup ? `${initial!.name}_copy` : "",
  );
  const [type, setType] = useState(initial?.type ?? knownTypes[0] ?? "mock");
  const [options, setOptions] = useState<Record<string, string>>(() => {
    const init = initial?.options ?? {};
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(init)) {
      out[k] = typeof v === "string" ? v : String(v);
    }
    return out;
  });
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const schema = TYPE_SCHEMAS[type] ?? null;

  const title =
    mode.kind === "add"
      ? "新建 provider entry"
      : mode.kind === "edit"
        ? `编辑 ${initial!.name}`
        : `复制 ${initial!.name}`;

  const submit = async () => {
    setErr(null);
    if (!isEdit && !name.trim()) {
      setErr("name 不能为空");
      return;
    }
    if (schema) {
      for (const f of schema) {
        if (f.required && !options[f.key]?.trim()) {
          setErr(`${f.label} 是必填字段`);
          return;
        }
      }
    }
    // 过滤空值(不发空串到 server,让 server 走默认值)
    const cleanOptions: Record<string, string> = {};
    for (const [k, v] of Object.entries(options)) {
      if (v.trim()) cleanOptions[k] = v;
    }
    setSubmitting(true);
    try {
      if (mode.kind === "add") {
        await api.createModel({ name: name.trim(), type, options: cleanOptions });
      } else if (mode.kind === "edit") {
        await api.updateModel(initial!.name, { type, options: cleanOptions });
      } else {
        // duplicate: server 复制源 entry,as=new-name;type/options/params 都不在请求里
        await api.duplicateModel(initial!.name, name.trim());
      }
      onSuccess();
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {mode.kind === "duplicate"
              ? "复制源 entry 的 type / options / params;只填新 name。如要改字段,先复制再 Edit。"
              : "字段按 type 切换;mock 无 options,anthropic 见下方表单。params 在行展开里改。"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {/* 模板预设(只在 add 模式显示;edit/duplicate 已有现成 entry,不需要)*/}
          {mode.kind === "add" && (
            <div className="rounded-md border border-border bg-muted/20 p-3">
              <div className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
                quick templates
              </div>
              <div className="flex flex-wrap gap-2">
                {TEMPLATES.map((tpl) => (
                  <Button
                    key={tpl.id}
                    variant="outline"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => {
                      setName(tpl.id);
                      setType(tpl.type);
                      setOptions((prev) => ({ ...prev, ...tpl.options }));
                    }}
                  >
                    {tpl.label}
                  </Button>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                点击模板自动填 name / type / options.model;api_key 仍需自己填(或留空走 env)。
              </p>
            </div>
          )}

          <FieldRow label="name">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isEdit}
              placeholder="user-friendly id(client 在 body.model 写这个)"
            />
          </FieldRow>

          <FieldRow label="type">
            <Select
              value={type}
              onValueChange={setType}
              disabled={isDup}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {knownTypes.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>

          {/* options 字段(按 type schema)— duplicate 模式不暴露(直接复制源 options) */}
          {!isDup && schema !== null && schema.length > 0 && (
            <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                options
              </div>
              {schema.map((f) => (
                <FieldRow key={f.key} label={f.label}>
                  <Input
                    type={f.isSecret ? "password" : "text"}
                    value={options[f.key] ?? ""}
                    onChange={(e) =>
                      setOptions({ ...options, [f.key]: e.target.value })
                    }
                    placeholder={f.placeholder}
                  />
                </FieldRow>
              ))}
            </div>
          )}

          {!isDup && schema !== null && schema.length === 0 && (
            <p className="rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
              该 type 不需要 options。
            </p>
          )}

          {!isDup && schema === null && (
            <p className="rounded-md border border-amber-300/30 bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">
              type <code className="font-mono">{type}</code> 没有 UI 表单 schema。
              建议用 CLI 加:
              <code className="ml-1 font-mono">chariot provider add --type {type} -o key=value</code>
            </p>
          )}

          {err && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
              {err}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}

// ---------- DeleteDialog ----------

function DeleteDialog({
  name,
  onClose,
  onSuccess,
}: {
  name: string;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const confirm = async () => {
    setErr(null);
    setSubmitting(true);
    try {
      await api.deleteModel(name);
      onSuccess();
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除 {name}?</AlertDialogTitle>
          <AlertDialogDescription>
            此 entry 会从 DB 移除,不可撤销。Chat 页若上次选的就是它,刷新后会自动落回第一条。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {err && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
            {err}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault();
              void confirm();
            }}
            disabled={submitting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {submitting ? "Deleting…" : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
