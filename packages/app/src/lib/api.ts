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
  capabilities: ProviderCapabilities;
  default?: boolean;
}

export type ProviderEntry = Provider;

export interface ProviderCapabilities {
  supports_system: boolean;
  supports_tools: boolean;
  supports_tool_choice: boolean;
  supports_thinking: boolean;
}

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

export interface ProviderStatusResponse {
  default_provider: string | null;
  provider_count: number;
  known_types: string[];
  providers: Provider[];
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

export interface PromptLayer {
  name: string;
  source: string;
  content: unknown;
}

export interface PromptBundle {
  id: string;
  name: string;
  description: string | null;
  layers: PromptLayer[];
  is_active: boolean;
  version_count: number;
  active_version: string | null;
  created_at: string;
  updated_at: string;
}

export interface PromptVersion {
  id: string;
  bundle_id: string;
  bundle_name: string;
  version: string;
  spec: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptTrace {
  id: string;
  bundle_id: string;
  bundle_name: string;
  version_id: string;
  version: string;
  conversation_id: string | null;
  provider_name: string;
  model: string | null;
  request: Record<string, unknown>;
  source_refs: Array<Record<string, unknown>>;
  prompt_size: number;
  created_at: string;
}

export interface ContextSlice {
  name: string;
  source: string;
  content: unknown;
}

export interface ContextSnapshot {
  id: string;
  conversation_id: string | null;
  provider_name: string;
  model: string | null;
  request: Record<string, unknown>;
  slices: ContextSlice[];
  source_refs: Array<Record<string, unknown>>;
  context_size: number;
  created_at: string;
}

export interface ContextTrace {
  id: string;
  snapshot_id: string;
  conversation_id: string | null;
  provider_name: string;
  model: string | null;
  prompt_trace_id: string | null;
  policy: Record<string, unknown>;
  selected_refs: Array<Record<string, unknown>>;
  created_at: string;
}

export interface ContextInspectResult {
  snapshot: ContextSnapshot | null;
  trace: ContextTrace | null;
}

export interface PromptBundleDetail extends PromptBundle {
  versions?: PromptVersion[];
}

export interface PromptBundlePayload {
  name: string;
  description?: string | null;
  layers?: PromptLayer[];
}

export interface PromptActivatePayload {
  name: string;
  version?: string | null;
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

  showTool(name: string): Promise<{ tool: Tool }> {
    return rpc("show_tool", { name });
  },

  probeTool(name: string): Promise<{ ok: boolean; latency_ms: number; error: ProbeError | null }> {
    return rpc("probe_tool", { name });
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

  showProvider(name: string): Promise<{ provider: Provider }> {
    return rpc("show_provider", { name });
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

  getProviderStatus(): Promise<ProviderStatusResponse> {
    return rpc("get_provider_status");
  },

  listLogs(params: ListLogsParams = {}): Promise<{ logs: LogEntry[] }> {
    return rpc("list_logs", { ...params });
  },

  listPromptBundles(): Promise<{ bundles: PromptBundle[] }> {
    return rpc("list_prompt_bundles");
  },

  getPromptBundle(name: string): Promise<{ bundle: PromptBundleDetail }> {
    return rpc("get_prompt_bundle", { name });
  },

  listPromptVersions(bundle_name: string): Promise<{ versions: PromptVersion[] }> {
    return rpc("list_prompt_versions", { bundle_name });
  },

  getPromptVersion(bundle_name: string, version: string): Promise<{ version: PromptVersion }> {
    return rpc("get_prompt_version", { bundle_name, version });
  },

  listPromptTraces(params: { bundle_name?: string; limit?: number; offset?: number } = {}): Promise<{ traces: PromptTrace[] }> {
    return rpc("list_prompt_traces", { ...params });
  },

  inspectPrompt(trace_id: string): Promise<{ trace: PromptTrace }> {
    return rpc("inspect_prompt", { trace_id });
  },

  listContextSnapshots(params: { conversation_id?: string | null; limit?: number; offset?: number } = {}): Promise<{
    snapshots: ContextSnapshot[];
  }> {
    return rpc("list_context_snapshots", { ...params });
  },

  getContextSnapshot(snapshot_id: string): Promise<{ snapshot: ContextSnapshot }> {
    return rpc("get_context_snapshot", { snapshot_id });
  },

  listContextTraces(params: { conversation_id?: string | null; limit?: number; offset?: number } = {}): Promise<{
    traces: ContextTrace[];
  }> {
    return rpc("list_context_traces", { ...params });
  },

  inspectContext(context_id: string): Promise<ContextInspectResult> {
    return rpc("inspect_context", { context_id });
  },

  addPromptBundle(payload: PromptBundlePayload): Promise<{ bundle: PromptBundle; version: PromptVersion }> {
    return rpc("add_prompt_bundle", payload as unknown as Record<string, unknown>);
  },

  updatePromptBundle(
    payload: PromptBundlePayload,
  ): Promise<{ bundle: PromptBundle; version: PromptVersion }> {
    return rpc("update_prompt_bundle", payload as unknown as Record<string, unknown>);
  },

  activatePromptBundle(payload: PromptActivatePayload): Promise<{ bundle: PromptBundle; version: PromptVersion | null }> {
    return rpc("activate_prompt_bundle", payload as unknown as Record<string, unknown>);
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
