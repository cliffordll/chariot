/**
 * chariot server admin API 薄封装。
 *
 * - 浏览器 / vite dev:相对路径 `/admin/*` + vite.config proxy 转到 server
 * - Tauri 壳内:webview origin 是 `https://tauri.localhost`,与 server 的
 *   `http://127.0.0.1:<port>` 跨 origin → 启动时 invoke `get_server_url`
 *   拿 base URL,之后所有 fetch 都 prepend
 * - 类型手写,对齐 `chariot/server/controller/*.py` 的 Pydantic schema
 *
 * 0.3.1 路由模型重构:active 概念删除;client 在 body.model 写 entry name
 * 直接路由,server 不再持有 active 状态。
 */

import { invoke } from "@tauri-apps/api/core";

export interface StatusResponse {
  version: string;
  uptime_ms: number;
  /** 0.3.1 起返已注册 entries 数量(active 退役)。 */
  entries_count: number;
  /** 客户端抵达 server 的 base URL(含 scheme + host + port)。 */
  url: string;
}

/** `GET /admin/logs` 单条。对齐 `chariot.server.controller.logs.LogOut`。 */
export interface LogOut {
  id: string;
  created_at: string;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  status: string;
  error: string | null;
}

export interface ListLogsParams {
  limit?: number;
  offset?: number;
  /** polling 游标:只取 `created_at > since` 的记录(ISO 8601)。 */
  since?: string;
}

/** `GET /admin/models` 响应。对齐 `chariot.server.controller.models.ModelsListResponse`。 */
export interface ModelsListResponse {
  /** entry name 列表(0.2.x 兼容字段)。 */
  available: string[];
  /** ModelRegistry 已注册的 type key 列表(mock / anthropic / ...)。 */
  types: string[];
  /** 完整 entries(name / type / options / params)。 */
  entries: ModelEntry[];
}

/** 探针失败时的错误结构。对齐 `chariot.server.service.model_prober.ProbeError`。 */
export interface ProbeError {
  /** "config_error" | "upstream_auth_failed" | "upstream_unreachable" | "upstream_timeout" | "upstream_server_error" | ... */
  code: string;
  message: string;
}

/** `POST /admin/models/{name}/probe` 响应。对齐 `chariot.server.service.model_prober.ProbeResult`。 */
export interface ProbeResult {
  ok: boolean;
  latency_ms: number;
  error: ProbeError | null;
}

/** `models` 表 CRUD 接口的 entry payload(对齐 `EntryResponse`)。 */
export interface ModelEntry {
  name: string;
  type: string;
  /** 按 type schema 形态的 dict;build Model 实例所需(model / api_key / ...)。 */
  options: Record<string, unknown>;
  /** 0.3.1 加。runtime 默认 sampling 参数(temperature / top_p / max_tokens 等);前端切到该 entry 时填充 Chat 高级参数面板。 */
  params: Record<string, unknown>;
}

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string) {
    super(`HTTP ${status}: ${body.slice(0, 200)}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Tauri 壳内 true / vite dev 浏览器 false。 */
function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

let basePromise: Promise<string> | null = null;

/** 解析 server base URL。Tauri 内 invoke `get_server_url`;浏览器返 ""(走 vite proxy)。
 *  失败(endpoint.json 未写)会抛,调用方照常展示错误。 */
export async function apiBase(): Promise<string> {
  if (!inTauri()) return "";
  if (!basePromise) {
    basePromise = invoke<string>("get_server_url")
      .then((url) => url.replace(/\/$/, ""))
      .catch((e) => {
        basePromise = null; // 失败不缓存,允许重试
        throw e;
      });
  }
  return basePromise;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await apiBase();
  const resp = await fetch(base + path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new ApiError(resp.status, text);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  ping(): Promise<{ ok: boolean }> {
    return request("/admin/ping");
  },
  status(): Promise<StatusResponse> {
    return request("/admin/status");
  },
  listLogs(params: ListLogsParams = {}): Promise<LogOut[]> {
    const q = new URLSearchParams();
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    if (params.since) q.set("since", params.since);
    const qs = q.toString();
    return request(`/admin/logs${qs ? "?" + qs : ""}`);
  },
  listModels(): Promise<ModelsListResponse> {
    return request("/admin/models");
  },
  /**
   * 对指定 model 跑一次探针。**真打上游一次,消耗 ~1 token 费用**(MockModel 零费用)。
   * name 不存在 → 404 ApiError;其它情况服务端一律包成 ProbeResult,error=null 表示通。
   */
  probeModel(name: string): Promise<ProbeResult> {
    return request(`/admin/models/${encodeURIComponent(name)}/probe`, {
      method: "POST",
    });
  },

  // ---------- entries CRUD(0.3.0)----------

  /**
   * 新建 entry。错误:`name_exists` 409 / `unknown_type` 400 / `bad_request` 400。
   */
  createModel(req: {
    name: string;
    type: string;
    options: Record<string, unknown>;
    params?: Record<string, unknown>;
  }): Promise<ModelEntry> {
    return request("/admin/models/entries", {
      method: "POST",
      body: JSON.stringify(req),
    });
  },

  /**
   * 编辑 entry。改 options 后 server 立即 rebuild 该 entry 的 Model 实例;改 params
   * 不触发 rebuild(params 只读暴露给前端用,不影响 build)。
   * 错误:`model_not_found` 404 / `unknown_type` 400 / `rebuild_failed` 502。
   */
  updateModel(
    name: string,
    req: {
      type?: string;
      options?: Record<string, unknown>;
      params?: Record<string, unknown>;
    },
  ): Promise<ModelEntry> {
    return request(`/admin/models/entries/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(req),
    });
  },

  /**
   * 删 entry。0.3.1 起 active 概念删除,任意 entry 都能删。`model_not_found` 404。
   */
  deleteModel(name: string): Promise<void> {
    return request(`/admin/models/entries/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  },

  /**
   * 复制 entry。`as` 缺省 `<name>_copy`,碰撞自动 `_copy_2 / _3`。
   * 错误:`model_not_found` 404(src 不存在)/ `name_exists` 409(指定 as 冲突)。
   */
  duplicateModel(name: string, as_?: string): Promise<ModelEntry> {
    return request(`/admin/models/entries/${encodeURIComponent(name)}/duplicate`, {
      method: "POST",
      body: JSON.stringify(as_ !== undefined ? { as: as_ } : {}),
    });
  },
};
