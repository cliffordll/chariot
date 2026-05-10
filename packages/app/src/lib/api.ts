import { invoke } from "@tauri-apps/api/core";

export class RpcError extends Error {
  code: number;

  constructor(code: number, message: string) {
    super(`rpc ${code}: ${message}`);
    this.name = "RpcError";
    this.code = code;
  }
}

export async function rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  try {
    return await invoke<T>("rpc", { method, params });
  } catch (e) {
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

export interface Conversation {
  id: string;
  title: string | null;
  last_model: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

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

export interface Message {
  role: "user" | "assistant";
  content: string | AnthropicBlock[];
  seq?: number;
  created_at?: string;
  provider_name?: string | null;
}

export interface Tool {
  name: string;
  type: string;
  enabled: boolean;
  options: Record<string, unknown>;
  schema_?: Record<string, unknown> | null;
}

export interface Provider {
  name: string;
  type: string;
  options: Record<string, unknown>;
  params: Record<string, unknown>;
  default?: boolean;
}

export type ProviderEntry = Provider;

export interface ProbeError {
  code: string;
  message: string;
}

export interface ProbeResult {
  ok: boolean;
  latency_ms: number;
  error: ProbeError | null;
}

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

export interface ListLogsParams {
  limit?: number;
  offset?: number;
  since?: string;
  until?: string;
}

export interface ConversationsListResponse {
  conversations: Conversation[];
  limit: number;
  offset: number;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
}

export interface ProvidersListResponse {
  available: string[];
  types: string[];
  entries: Provider[];
}

export interface ToolsListResponse {
  types: string[];
  entries: Tool[];
}

export interface StatusResponse {
  version: string;
  uptime_ms: number;
  entries_count: number;
  tools_enabled: number;
  conversations_count: number;
  url: string;
}

const apiCore = {
  listConversations(): Promise<{ conversations: Conversation[] }> {
    return rpc("list_conversations");
  },

  getConversation(conversation_id: string): Promise<{ conversation: Conversation; messages: Message[] }> {
    return rpc("get_conversation", { conversation_id });
  },

  renameConversation(conversation_id: string, title: string | null): Promise<{ conversation: Conversation }> {
    return rpc("rename_conversation", { conversation_id, title });
  },

  deleteConversation(conversation_id: string): Promise<{ deleted: string }> {
    return rpc("delete_conversation", { conversation_id });
  },

  createConversation(req: { id?: string; title?: string | null } = {}): Promise<Conversation> {
    const id = req.id ?? generateUlid();
    const now = new Date().toISOString();
    return Promise.resolve({
      id,
      title: req.title ?? null,
      last_model: null,
      message_count: 0,
      created_at: now,
      updated_at: now,
    });
  },

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

  updateTool(name: string, req: { enabled?: boolean; options?: Record<string, unknown> }): Promise<{ tool: Tool }> {
    if (req.options !== undefined) {
      return apiCore.configTool(name, req.options);
    }
    if (req.enabled === true) {
      return apiCore.enableTool(name);
    }
    if (req.enabled === false) {
      return apiCore.disableTool(name);
    }
    return apiCore.configTool(name, {});
  },

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

  updateProvider(
    name: string,
    req: {
      type?: string;
      options?: Record<string, unknown>;
      params?: Record<string, unknown>;
    },
  ): Promise<{ provider: Provider }> {
    return rpc("update_provider", { name, ...req });
  },

  deleteProvider(name: string): Promise<{ deleted: string }> {
    return rpc("delete_provider", { name });
  },

  async duplicateProvider(name: string, as_?: string): Promise<{ provider: Provider }> {
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
    return { provider };
  },

  probeProvider(name: string): Promise<ProbeResult> {
    return rpc("probe_provider", { name });
  },

  listLogs(params: ListLogsParams = {}): Promise<{ logs: LogEntry[] }> {
    return rpc("list_logs", { ...params });
  },

  async status(): Promise<StatusResponse> {
    const [{ providers }, { tools }, { conversations }] = await Promise.all([
      apiCore.listProviders(),
      apiCore.listTools(),
      apiCore.listConversations(),
    ]);
    return {
      version: "0.6.5",
      uptime_ms: 0,
      entries_count: providers.length,
      tools_enabled: tools.filter((t) => t.enabled).length,
      conversations_count: conversations.length,
      url: "stdio://sidecar",
    };
  },

  async ping(): Promise<{ ok: true }> {
    await apiCore.listTools();
    return { ok: true };
  },
} as const;

export const api = apiCore;

function generateUlid(): string {
  const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
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

export const ApiError = RpcError;
export type ApiError = RpcError;
