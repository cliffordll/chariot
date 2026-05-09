/**
 * Chariot 前端 API 层(0.6.5 S.10 起 · stdio JSON-RPC)。
 *
 * 跟 0.5.0 的差异:
 * - 撤所有 `fetch("/admin/...")` HTTP 调用 + endpoint.json 解析
 * - 改走 Tauri `invoke("rpc", { method, params })` → Rust JsonRpcClient → sidecar
 * - 类型对齐 `chariot/sidecar/methods/*.py` 各 method 的 wire payload(不再对齐
 *   旧 `chariot/server/controller/*.py` Pydantic schema)
 * - 字段命名 0.6.0 rename:
 *   - `Conversation` → `Convo`,接口的 `conversations` → `convos`
 *   - `Model` 概念整体 rename `Provider`(`listModels` → `listProviders` 等)
 *   - `LogOut.model` → `LogEntry.provider`
 *   - `Message.model_name` → `provider_name`
 *
 * RPC method 名(对齐 sidecar `register_methods`,详 `chariot/sidecar/methods/__init__.py`):
 *   chat / list_convos / get_convo / rename_convo / delete_convo /
 *   list_tools / enable_tool / disable_tool / config_tool /
 *   list_providers / add_provider / edit_provider / delete_provider / probe_provider /
 *   list_logs
 */

import { invoke } from "@tauri-apps/api/core";

// ============================================================
// RPC 通用层
// ============================================================

/** RPC 协议错误(对应 Rust `RpcError`)。 */
export class RpcError extends Error {
  /** JSON-RPC 错误码;具体值见 `chariot.rpc.jsonrpc.JsonRpcServer.ERR_*`。 */
  code: number;

  constructor(code: number, message: string) {
    super(`rpc ${code}: ${message}`);
    this.name = "RpcError";
    this.code = code;
  }
}

/**
 * Tauri `invoke("rpc", ...)` 薄封装。
 *
 * `params` 推 sidecar 时序列化为 JSON object;返 server response.result。
 * RpcError 走 Tauri error 通道传上来,这里 unwrap 成 `RpcError` 实例 throw。
 */
export async function rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  try {
    return await invoke<T>("rpc", { method, params });
  } catch (e) {
    // Tauri 把 RpcError(serde Serialize)序成 `{ Server: { code, message } }` 形态
    if (typeof e === "object" && e !== null && "Server" in e) {
      const inner = (e as { Server: { code: number; message: string } }).Server;
      throw new RpcError(inner.code, inner.message);
    }
    if (typeof e === "object" && e !== null && "SidecarExited" in e) {
      const reason = (e as { SidecarExited: string }).SidecarExited;
      throw new RpcError(-1, `sidecar exited: ${reason}`);
    }
    if (typeof e === "string") throw new Error(e);
    throw e;
  }
}

// ============================================================
// 类型(对齐 chariot/sidecar/methods/* 的 wire payload)
// ============================================================

/** 对齐 `chariot.sidecar.methods.convo._ConvoMethods._serialize`。 */
export interface Convo {
  id: string;
  title: string | null;
  /** 派生:最后一轮 assistant 用的 provider entry name。 */
  last_model: string | null;
  /** ISO 8601 datetime。 */
  created_at: string;
  updated_at: string;
  message_count: number;
}

/** Anthropic content block(text / tool_use / tool_result / 其它);content
 *  可以是 string(纯文本 message)或 blocks 数组。前端按 type 分支渲染。 */
export type AnthropicBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | {
      type: "tool_result";
      tool_use_id: string;
      content: AnthropicBlock[] | string;
      is_error?: boolean;
    }
  | { type: string; [key: string]: unknown };

/** convo 内一条消息(`get_convo` 返的 `messages[]`,Anthropic 协议形态)。
 *
 *  0.5.0 时 Message 还含 `seq` / `created_at` / `model_name`,0.6.5 sidecar
 *  返的 `messages[]` 只有 `{role, content}`。这里把老字段标 optional,老 page
 *  仍能 read(undefined),0.7.0+ 加回完整字段时再变 required。
 */
export interface Message {
  role: "user" | "assistant";
  content: string | AnthropicBlock[];
  /** 0.5.0 字段:`messages.seq`(单调 0 起);0.6.5 sidecar 不返。 */
  seq?: number;
  /** 0.5.0 字段:ISO datetime;0.6.5 sidecar 不返。 */
  created_at?: string;
  /** 0.5.0 字段(老命名 model_name);0.6.5 sidecar 不返。仅 role='assistant' 行非空。 */
  model_name?: string | null;
  /** 0.6.0 重命名 `model_name` → `provider_name`;同样 0.6.5 sidecar 不返。 */
  provider_name?: string | null;
}

/** 对齐 `chariot.sidecar.methods.tool._ToolMethods._serialize`。 */
export interface Tool {
  name: string;
  type: string;
  enabled: boolean;
  options: Record<string, unknown>;
  /** 0.5.0 字段:Anthropic tool definition JSON。0.6.5 sidecar 不返(0.7.0+ 加),
   *  老 page 仍读这字段,这里固定 undefined / null,UI 显示"schema not available"。 */
  schema_?: Record<string, unknown> | null;
}

/** 对齐 `chariot.sidecar.methods.provider._ProviderMethods._serialize`(加 default 字段)。 */
export interface Provider {
  name: string;
  type: string;
  options: Record<string, unknown>;
  params: Record<string, unknown>;
  /** 仅 `list_providers` 返的列表项有此字段(单个 entry 的 `_serialize` 不带)。 */
  default?: boolean;
}

/** 探针失败时的错误结构。 */
export interface ProbeError {
  code: string;
  message: string;
}

/** 对齐 `probe_provider` 返。 */
export interface ProbeResult {
  ok: boolean;
  latency_ms: number;
  error: ProbeError | null;
}

/** 对齐 `chariot.sidecar.methods.log._LogMethods._serialize`。 */
export interface LogEntry {
  id: string;
  provider: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  status: string;
  error: string | null;
  created_at: string;
}

/** `list_logs` 参数(0.6.5 sidecar);limit 默认 100,上限 1000。 */
export interface ListLogsParams {
  limit?: number;
  offset?: number;
  /** ISO 8601 datetime,严格大于(polling 游标)。 */
  since?: string;
  until?: string;
}

// ============================================================
// API surface
// ============================================================

/**
 * `api.X(...)` 形态保留(给 page 层调用),内部走 RPC。
 *
 * **这一层就是 RPC 命名翻译 + 强类型** — 0.6.5 sidecar method 名是 snake_case
 * (`list_convos` / `add_provider`),前端按 camelCase 暴露(`listConvos` /
 * `addProvider`)。
 */
const apiCore = {
  // ---------- convos ----------

  listConvos(): Promise<{ convos: Convo[] }> {
    return rpc("list_convos");
  },

  getConvo(convo_id: string): Promise<{ convo: Convo; messages: Message[] }> {
    return rpc("get_convo", { convo_id });
  },

  renameConvo(convo_id: string, title: string | null): Promise<{ convo: Convo }> {
    return rpc("rename_convo", { convo_id, title });
  },

  deleteConvo(convo_id: string): Promise<{ deleted: string }> {
    return rpc("delete_convo", { convo_id });
  },

  // ---------- tools ----------

  listTools(): Promise<{ tools: Tool[] }> {
    return rpc("list_tools");
  },

  enableTool(name: string): Promise<{ tool: Tool }> {
    return rpc("enable_tool", { name });
  },

  disableTool(name: string): Promise<{ tool: Tool }> {
    return rpc("disable_tool", { name });
  },

  configTool(name: string, options: Record<string, unknown>): Promise<{ tool: Tool }> {
    return rpc("config_tool", { name, options });
  },

  // ---------- providers ----------

  listProviders(): Promise<{ providers: Provider[] }> {
    return rpc("list_providers");
  },

  addProvider(req: {
    name: string;
    type: string;
    options: Record<string, unknown>;
    params?: Record<string, unknown>;
  }): Promise<{ provider: Provider }> {
    return rpc("add_provider", { ...req });
  },

  editProvider(
    name: string,
    req: {
      type?: string;
      options?: Record<string, unknown>;
      params?: Record<string, unknown>;
    },
  ): Promise<{ provider: Provider }> {
    return rpc("edit_provider", { name, ...req });
  },

  deleteProvider(name: string): Promise<{ deleted: string }> {
    return rpc("delete_provider", { name });
  },

  /**
   * 对指定 provider 跑一次探针。**真打上游一次**(MockProvider 零费用)。
   * name 不存在 → ERR_NOT_FOUND;其它情况一律包成 ProbeResult,error=null 表示通。
   */
  probeProvider(name: string): Promise<ProbeResult> {
    return rpc("probe_provider", { name });
  },

  // ---------- logs ----------

  listLogs(params: ListLogsParams = {}): Promise<{ logs: LogEntry[] }> {
    return rpc("list_logs", { ...params });
  },
} as const;

// ============================================================
// Transition aliases(0.6.5 S.10 → S.12 之间保留;S.12 page 全切完后清)
// ============================================================
//
// 0.5.0 page 大量用 listModels / listConversations / createConversation 等老
// 命名;S.10 引 sidecar 后 RPC method 名跟着 0.6.0 rename 改。为避免一次性
// 把 5 个 page 全改完,这里加薄 alias 层 — 老函数名内部走新 RPC。
//
// 已知不完美:
// - sidecar 没有 `status` / `ping` method,这俩返 hardcoded stub(prod 真信号
//   靠 sidecar_exited Tauri event,见 ServerStatusBanner)
// - sidecar 没有 `create_convo` method,客户端生成 ULID 当 convo_id;首次 chat
//   时 sidecar 自动 create
// - sidecar 没有 `duplicate_provider`,这里用 listProviders + addProvider 客户端
//   合成
// - sidecar 没有 listConvos pagination,返全量,limit/offset 客户端切

export type Conversation = Convo;

export interface ConversationsListResponse {
  items: Convo[];
  limit: number;
  offset: number;
}

/** 0.5.0 Conversation 详情形态;0.6.5 sidecar 返 `{convo, messages}`。 */
export interface ConversationDetail {
  conversation: Convo;
  /** Stub 字段:provider_name / seq / created_at 0.6.5 sidecar 不返,这里固定 null/0/"" */
  messages: Array<Message & { provider_name: string | null; seq: number; created_at: string }>;
}

export type ModelEntry = Provider;
/** 跟 `ModelEntry` 等价,新代码用这个名字(Provider 概念已经替代 Model)。 */
export type ProviderEntry = Provider;

export interface ModelsListResponse {
  /** entry name 列表(0.2.x 兼容字段)。 */
  available: string[];
  /** ProviderRegistry 已注册的 type key 列表(mock / anthropic / ...);sidecar 暂不返,固定空数组。 */
  types: string[];
  entries: Provider[];
}
/** 跟 `ModelsListResponse` 等价,新代码用这个名字。 */
export type ProvidersListResponse = ModelsListResponse;

export interface ToolsListResponse {
  /** ToolRegistry.known_types();sidecar 暂不返,固定空数组。 */
  types: string[];
  entries: Tool[];
}

export type LogOut = LogEntry;

/** 老 ApiError(0.5.0 HTTP 错);S.10 起统一 RpcError(老 page `instanceof ApiError` 仍工作)。 */
export const ApiError = RpcError;
export type ApiError = RpcError;

export interface StatusResponse {
  version: string;
  uptime_ms: number;
  entries_count: number;
  tools_enabled: number;
  conversations_count: number;
  url: string;
}

// ---- 兼容方法挂在 api 上 ----
// 不能用 const enum 扩展,改用 Object.assign / 直接挂

interface ApiCompat {
  listConversations: (params?: { limit?: number; offset?: number }) => Promise<ConversationsListResponse>;
  getConversation: (id: string) => Promise<ConversationDetail>;
  createConversation: (req?: { id?: string; title?: string | null }) => Promise<Convo>;
  deleteConversation: (id: string) => Promise<void>;
  updateConversationTitle: (id: string, title: string | null) => Promise<Convo>;
  listModels: () => Promise<ModelsListResponse>;
  probeModel: (name: string) => Promise<ProbeResult>;
  createModel: (req: {
    name: string;
    type: string;
    options: Record<string, unknown>;
    params?: Record<string, unknown>;
  }) => Promise<Provider>;
  updateModel: (
    name: string,
    req: { type?: string; options?: Record<string, unknown>; params?: Record<string, unknown> },
  ) => Promise<Provider>;
  deleteModel: (name: string) => Promise<void>;
  duplicateModel: (name: string, as_?: string) => Promise<Provider>;
  updateTool: (name: string, req: { enabled?: boolean; options?: Record<string, unknown> }) => Promise<Tool>;
  status: () => Promise<StatusResponse>;
  ping: () => Promise<{ ok: true }>;
}

const apiCompat: ApiCompat = {
  async listConversations(params = {}) {
    const { convos } = await apiCore.listConvos();
    const offset = params.offset ?? 0;
    const limit = params.limit ?? convos.length;
    return { items: convos.slice(offset, offset + limit), limit, offset };
  },

  async getConversation(id) {
    const { convo, messages } = await apiCore.getConvo(id);
    return {
      conversation: convo,
      messages: messages.map((m) => ({ ...m, provider_name: null, seq: 0, created_at: "" })),
    };
  },

  async createConversation(req = {}) {
    // sidecar 不暴露 create_convo;客户端生成 ULID,首次 chat 时自动创建
    const id = req.id ?? generateUlid();
    const now = new Date().toISOString();
    return {
      id,
      title: req.title ?? null,
      last_model: null,
      message_count: 0,
      created_at: now,
      updated_at: now,
    };
  },

  async deleteConversation(id) {
    await apiCore.deleteConvo(id);
  },

  async updateConversationTitle(id, title) {
    const { convo } = await apiCore.renameConvo(id, title);
    return convo;
  },

  async listModels() {
    const { providers } = await apiCore.listProviders();
    return {
      available: providers.map((p) => p.name),
      types: [],
      entries: providers,
    };
  },

  probeModel(name) {
    return apiCore.probeProvider(name);
  },

  async createModel(req) {
    const { provider } = await apiCore.addProvider(req);
    return provider;
  },

  async updateModel(name, req) {
    const { provider } = await apiCore.editProvider(name, req);
    return provider;
  },

  async deleteModel(name) {
    await apiCore.deleteProvider(name);
  },

  async duplicateModel(name, as_) {
    // sidecar 不暴露 duplicate_provider;客户端 list + add 合成
    const { providers } = await apiCore.listProviders();
    const src = providers.find((p) => p.name === name);
    if (!src) throw new RpcError(-32001, `provider ${name} not found`);
    const newName = (as_ ?? `${name}_copy`).trim();
    const { provider } = await apiCore.addProvider({
      name: newName,
      type: src.type,
      options: src.options,
      params: src.params,
    });
    return provider;
  },

  async updateTool(name, req) {
    let last: Tool | null = null;
    if (req.enabled !== undefined) {
      const { tool } = req.enabled ? await apiCore.enableTool(name) : await apiCore.disableTool(name);
      last = tool;
    }
    if (req.options !== undefined) {
      const { tool } = await apiCore.configTool(name, req.options);
      last = tool;
    }
    if (last === null) {
      // 都没改:返当前状态(由 listTools 找一遍)。极少出现。
      const { tools } = await apiCore.listTools();
      const found = tools.find((t) => t.name === name);
      if (!found) throw new RpcError(-32001, `tool ${name} not found`);
      last = found;
    }
    return last;
  },

  async status() {
    // sidecar 没 status method;返尽力而为的 stub(版本号客户端没法拿,占位)
    const [{ providers }, { tools }, { convos }] = await Promise.all([
      api.listProviders(),
      api.listTools(),
      api.listConvos(),
    ]);
    return {
      version: "0.6.5",
      uptime_ms: 0,
      entries_count: providers.length,
      tools_enabled: tools.filter((t) => t.enabled).length,
      conversations_count: convos.length,
      url: "stdio://sidecar",
    };
  },

  async ping() {
    // 通过 listTools 试探一次 RPC 通道(成功 = sidecar 在跑);失败由 caller catch
    await apiCore.listTools();
    return { ok: true };
  },
};

// 合并 core + compat 暴露给 page 层。运行时是单一对象,类型是两个的交集。
export const api: typeof apiCore & ApiCompat = { ...apiCore, ...apiCompat };

/**
 * 客户端 ULID 生成 — 26 字符 Crockford-Base32(time + random)。
 *
 * 0.6.5 S.10 起 sidecar 不暴露 `create_convo`,新建 convo 走"客户端生成 id +
 * 首次 chat 自动 ensure_exists"。简化版实现,不必跟服务端 ULID 严格一致,
 * 只要符合正则 `^[0-9A-Z]{26}$`(server 端校验)即可。
 */
function generateUlid(): string {
  const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; // Crockford(去掉 I L O U)
  const time = Date.now();
  let timePart = "";
  let t = time;
  for (let i = 0; i < 10; i++) {
    timePart = ALPHABET[t % 32] + timePart;
    t = Math.floor(t / 32);
  }
  let randPart = "";
  for (let i = 0; i < 16; i++) {
    randPart += ALPHABET[Math.floor(Math.random() * 32)];
  }
  return timePart + randPart;
}
