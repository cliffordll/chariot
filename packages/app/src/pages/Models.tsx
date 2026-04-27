import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

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
  type ModelEntry,
  type ModelsListResponse,
} from "@/lib/api";

/**
 * Models 页 —— 模型管理(0.3.0 全功能版)。
 *
 * 能力:
 * - 列出所有 entries(标 active)+ Test 按钮跑探针
 * - 新建 / 编辑 / 复制 / 删除 entry(走 admin/models/entries CRUD)
 * - 改 active 在 Chat 页(决策 7A:per-tab 切 active 仍在 Chat)
 *
 * 字段 schema 按 type 切表单(`TYPE_SCHEMAS`):mock 无字段,anthropic 给
 * model_id / api_key / api_key_env / base_url。未知 type 提示用 CLI -o 参数。
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
      key: "model_id",
      label: "model_id",
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

type ModelsState =
  | { kind: "loading" }
  | { kind: "ok"; data: ModelsListResponse }
  | { kind: "err"; message: string };

type ProbeState =
  | { kind: "idle" }
  | { kind: "probing" }
  | { kind: "ok"; latency: number }
  | { kind: "fail"; latency: number; code: string; message: string };

type DialogMode =
  | { kind: "closed" }
  | { kind: "add" }
  | { kind: "edit"; source: ModelEntry }
  | { kind: "duplicate"; source: ModelEntry };

type DeleteState = { open: false } | { open: true; name: string; isActive: boolean };

export default function Models() {
  const [modelsState, setModelsState] = useState<ModelsState>({ kind: "loading" });
  const [probeStates, setProbeStates] = useState<Record<string, ProbeState>>({});
  const [dialog, setDialog] = useState<DialogMode>({ kind: "closed" });
  const [del, setDel] = useState<DeleteState>({ open: false });
  const [entryDetails, setEntryDetails] = useState<Record<string, ModelEntry>>({});

  const load = useCallback(async () => {
    setModelsState({ kind: "loading" });
    try {
      const data = await api.listModels();
      setModelsState({ kind: "ok", data });
      // 拉每条 entry 的完整 detail(name 列表外还要 type / options)
      // listModels 当前只返 name 列表;edit/duplicate 时需要 options,改下面分开请求
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setModelsState({ kind: "err", message: msg });
    }
  }, []);

  /**
   * 从 server 缓存的 ChariotConfig 拿不到 options(GET /admin/models 只返 name 列表),
   * edit / duplicate 需要 options,临时方案:用 probe 也要先 build entry,这条信息其实在
   * server 内部已有。0.3.0 简化:edit / duplicate 时调一次 server 拿 entry 详情;
   * 但当前没单独的 GET /entries/{name} 端点。短期方案:让 listModels 一并返 entries
   * 详情(0.3.1 改),0.3.0 先用本地缓存(create / update / duplicate 后塞进
   * entryDetails,edit 时若没有则 fallback 弹空表单 + 提示)。
   */
  const cacheEntry = useCallback((entry: ModelEntry) => {
    setEntryDetails((prev) => ({ ...prev, [entry.name]: entry }));
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

  const onCreated = useCallback(
    (entry: ModelEntry) => {
      cacheEntry(entry);
      void load();
    },
    [cacheEntry, load],
  );

  const onDeleted = useCallback(() => {
    void load();
  }, [load]);

  const openEdit = useCallback(
    (name: string) => {
      const cached = entryDetails[name];
      // 如果没缓存(刷新后),传一个最小占位 — 用户可以重新填字段
      const source: ModelEntry = cached ?? {
        name,
        type: modelsState.kind === "ok" ? guessTypeFromAvailable() : "mock",
        options: {},
      };
      setDialog({ kind: "edit", source });
    },
    [entryDetails, modelsState],
  );

  const openDuplicate = useCallback(
    (name: string) => {
      const cached = entryDetails[name];
      const source: ModelEntry = cached ?? {
        name,
        type: "mock",
        options: {},
      };
      setDialog({ kind: "duplicate", source });
    },
    [entryDetails],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Models</h1>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setDialog({ kind: "add" })}>
            + Add
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            Refresh
          </Button>
        </div>
      </div>

      <ModelsCard
        modelsState={modelsState}
        probeStates={probeStates}
        onProbe={runProbe}
        onEdit={openEdit}
        onDuplicate={openDuplicate}
        onDelete={(name, isActive) => setDel({ open: true, name, isActive })}
      />

      {dialog.kind !== "closed" && (
        <AddEditDialog
          mode={dialog}
          knownTypes={modelsState.kind === "ok" ? modelsState.data.types : []}
          onClose={() => setDialog({ kind: "closed" })}
          onSuccess={onCreated}
        />
      )}

      {del.open && (
        <DeleteDialog
          name={del.name}
          isActive={del.isActive}
          onClose={() => setDel({ open: false })}
          onSuccess={() => {
            setDel({ open: false });
            onDeleted();
          }}
        />
      )}
    </section>
  );
}

function guessTypeFromAvailable(): string {
  return "mock";
}

// ---------- 列表卡片 ----------

function ModelsCard({
  modelsState,
  probeStates,
  onProbe,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  modelsState: ModelsState;
  probeStates: Record<string, ProbeState>;
  onProbe: (name: string) => void;
  onEdit: (name: string) => void;
  onDuplicate: (name: string) => void;
  onDelete: (name: string, isActive: boolean) => void;
}) {
  if (modelsState.kind === "loading") {
    return (
      <div className="max-w-3xl rounded-lg border border-border p-4 text-sm text-muted-foreground">
        Loading models…
      </div>
    );
  }
  if (modelsState.kind === "err") {
    return (
      <div className="max-w-3xl rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        无法读取 model 列表:{modelsState.message}
      </div>
    );
  }
  const { data } = modelsState;

  return (
    <div className="max-w-3xl rounded-lg border border-border p-4">
      <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
        active
      </div>
      <div className="mb-4 text-sm">
        {data.active ? (
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{data.active}</code>
        ) : (
          <span className="text-muted-foreground">(none — MockModel fallback)</span>
        )}
        <Link
          to="/chat"
          className="ml-3 text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          切 active 在 Chat 页 →
        </Link>
      </div>

      <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
        available
      </div>
      {data.available.length === 0 ? (
        <p className="mb-4 text-xs text-muted-foreground">
          DB 里没有 entry。点击右上角 <code className="font-mono">+ Add</code> 新建一条。
        </p>
      ) : (
        <>
          <div className="mb-2 text-xs text-muted-foreground">
            Test 会真打上游一次,消耗 ~1 token;mock 模型走本地零费用。
          </div>
          <ul className="mb-4 divide-y divide-border rounded-md border border-border">
            {data.available.map((name) => (
              <ModelRow
                key={name}
                name={name}
                isActive={name === data.active}
                state={probeStates[name] ?? { kind: "idle" }}
                onProbe={() => onProbe(name)}
                onEdit={() => onEdit(name)}
                onDuplicate={() => onDuplicate(name)}
                onDelete={() => onDelete(name, name === data.active)}
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

function ModelRow({
  name,
  isActive,
  state,
  onProbe,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  name: string;
  isActive: boolean;
  state: ProbeState;
  onProbe: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
      <code className="font-mono">{name}</code>
      {isActive && <Badge className="h-5 px-1.5 text-[10px]">active</Badge>}
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
    </li>
  );
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
  onSuccess: (entry: ModelEntry) => void;
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
    mode.kind === "add" ? "新建 model entry"
      : mode.kind === "edit" ? `编辑 ${initial!.name}`
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
      let entry: ModelEntry;
      if (mode.kind === "add") {
        entry = await api.createModel({ name: name.trim(), type, options: cleanOptions });
      } else if (mode.kind === "edit") {
        entry = await api.updateModel(initial!.name, { type, options: cleanOptions });
      } else {
        // duplicate: server 复制源 entry,as=new-name;type/options 不在请求里
        entry = await api.duplicateModel(initial!.name, name.trim());
      }
      onSuccess(entry);
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
              ? "复制源 entry 的 type 和 options;只填新 name。如要改 options,先复制再 Edit。"
              : "字段按 type 切换;mock 无 options,anthropic 见下方表单。"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <FieldRow label="name">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isEdit}
              placeholder="user-friendly id"
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
              <code className="ml-1 font-mono">chariot model add --type {type} -o key=value</code>
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
  isActive,
  onClose,
  onSuccess,
}: {
  name: string;
  isActive: boolean;
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
            {isActive
              ? `${name} 是当前 active model,server 会拒绝删除。请先在 Chat 页切到别的 entry,再来这里删。`
              : "此 entry 会从 DB 移除,不可撤销。已发出的请求不受影响。"}
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
            disabled={submitting || isActive}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {submitting ? "Deleting…" : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
