/**
 * chariot server admin API 薄封装。
 *
 * - 浏览器 / vite dev:相对路径 `/admin/*` + vite.config proxy 转到 server
 * - Tauri 壳内:webview origin 是 `https://tauri.localhost`,与 server 的
 *   `http://127.0.0.1:<port>` 跨 origin → 启动时 invoke `get_server_url`
 *   拿 base URL,之后所有 fetch 都 prepend
 * - 类型手写,对齐 `chariot/server/controller/*.py` 的 Pydantic schema
 *
 * 0.2.0 起 chariot 单协议化(只接 Anthropic Messages),client 不再需要
 * Protocol 枚举 / 三协议 model 候选;active model 在 server 端切换。
 */

import { invoke } from "@tauri-apps/api/core";

export interface StatusResponse {
  version: string;
  uptime_ms: number;
  /** 当前 agent 的 model 标识(默认 `mock-echo-v1`,真模型如 `anthropic`)。 */
  model: string;
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
  /** 配置文件里的 model name 列表(`[[models]] name = ...`)。 */
  available: string[];
  /** 当前激活的 model name;MockModel fallback 时为 null。 */
  active: string | null;
  /** ModelRegistry 已注册的 type key 列表(mock / anthropic / ...)。 */
  types: string[];
}

/** `POST /admin/models` 响应。对齐 `chariot.server.controller.models.SwitchModelResponse`。 */
export interface SwitchModelResponse {
  active: string;
  model: string;
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
  useModel(name: string): Promise<SwitchModelResponse> {
    return request("/admin/models", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
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
};
