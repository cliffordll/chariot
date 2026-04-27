import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, type ApiError, type ModelsListResponse } from "@/lib/api";

/**
 * Models 页 —— 模型管理(0.2.5 起独立成 tab)。
 *
 * 当前能力(0.2.5):
 * - 列出 config.toml 里的所有 [[models]] entry,标 active
 * - 对每条 entry 跑连通性探针(`api.probeModel`),展示通断 + latency + 错误码
 *
 * 后续(0.2.7+)会加:
 * - 切 active(目前在 Chat 页;0.2.7 把语义改成 per-tab 后,这里接管全局切换)
 * - CRUD model entries(0.2.8 起)
 *
 * 探针行为:server 临时 build entry,发 1 条最小 messages 请求(`max_tokens=1`,
 * 内容 `ping`)。MockModel 走本地零费用;真后端消耗 ~1 token。
 */
type ModelsState =
  | { kind: "loading" }
  | { kind: "ok"; data: ModelsListResponse }
  | { kind: "err"; message: string };

type ProbeState =
  | { kind: "idle" }
  | { kind: "probing" }
  | { kind: "ok"; latency: number }
  | { kind: "fail"; latency: number; code: string; message: string };

export default function Models() {
  const [modelsState, setModelsState] = useState<ModelsState>({ kind: "loading" });
  const [probeStates, setProbeStates] = useState<Record<string, ProbeState>>({});

  const load = useCallback(async () => {
    setModelsState({ kind: "loading" });
    try {
      const data = await api.listModels();
      setModelsState({ kind: "ok", data });
    } catch (e) {
      const msg = e instanceof Error ? (e as ApiError).message || e.message : String(e);
      setModelsState({ kind: "err", message: msg });
    }
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

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Models</h1>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      <ModelsCard
        modelsState={modelsState}
        probeStates={probeStates}
        onProbe={runProbe}
      />
    </section>
  );
}

function ModelsCard({
  modelsState,
  probeStates,
  onProbe,
}: {
  modelsState: ModelsState;
  probeStates: Record<string, ProbeState>;
  onProbe: (name: string) => void;
}) {
  if (modelsState.kind === "loading") {
    return (
      <div className="max-w-2xl rounded-lg border border-border p-4 text-sm text-muted-foreground">
        Loading models…
      </div>
    );
  }

  if (modelsState.kind === "err") {
    return (
      <div className="max-w-2xl rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        无法读取 model 列表:{modelsState.message}
      </div>
    );
  }

  const { data } = modelsState;

  return (
    <div className="max-w-2xl rounded-lg border border-border p-4">
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
          配置文件里没有 model。在{" "}
          <code className="font-mono">~/.chariot/config.toml</code> 加{" "}
          <code className="rounded bg-muted px-1 py-0.5">[[models]]</code> 后重启 server。
        </p>
      ) : (
        <>
          <div className="mb-2 text-xs text-muted-foreground">
            Test 会真打上游一次,消耗 ~1 token;mock 模型走本地零费用。
          </div>
          <ul className="mb-4 divide-y divide-border rounded-md border border-border">
            {data.available.map((name) => (
              <ProbeRow
                key={name}
                name={name}
                isActive={name === data.active}
                state={probeStates[name] ?? { kind: "idle" }}
                onProbe={() => onProbe(name)}
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

function ProbeRow({
  name,
  isActive,
  state,
  onProbe,
}: {
  name: string;
  isActive: boolean;
  state: ProbeState;
  onProbe: () => void;
}) {
  return (
    <li className="flex items-center gap-2 px-3 py-2 text-sm">
      <code className="font-mono">{name}</code>
      {isActive && <Badge className="h-5 px-1.5 text-[10px]">active</Badge>}
      <Button
        variant="outline"
        size="sm"
        className="ml-auto h-7 px-2 text-xs"
        onClick={onProbe}
        disabled={state.kind === "probing"}
      >
        {state.kind === "probing" ? "Testing…" : "Test"}
      </Button>
      <ProbeStatus state={state} />
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
