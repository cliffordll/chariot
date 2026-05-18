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
  agent_profile: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationSearchHit {
  message_id: string;
  conversation_id: string;
  role: string;
  snippet: string;
  rank: number;
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
  provider_snapshot?: string | null;
}

export interface Tool {
  name: string;
  type: string;
  enabled: boolean;
  options: Record<string, unknown>;
  source?: string;
  description?: string;
  custom_type?: string | null;
  schema_?: Record<string, unknown> | null;
}

export interface Provider {
  id: string;
  name: string;
  type: string;
  options: Record<string, unknown>;
  params: Record<string, unknown>;
  capabilities: ProviderCapabilities;
  health?: ProviderHealthSummary | null;
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

export interface ProviderHealthSummary {
  provider_snapshot: string;
  last_ok: boolean;
  latency_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  last_probe_at: string | null;
  updated_at: string | null;
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
  provider_snapshot: string;
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
  provider_snapshot: string;
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
  provider_snapshot: string;
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

export interface MemoryEntry {
  id: string;
  kind: string;
  text: string;
  meta: Record<string, unknown>;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoryEvent {
  id: string;
  memory_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface MemoryLink {
  id: string;
  memory_id: string;
  link_type: string;
  link_value: string;
  created_at: string;
}

// ---- B5 wave 4: Guardrails / Audit / Checkpoints / Capabilities ----

export interface GuardrailRule {
  rule_id: string;
  description: string;
  verdict: "allow" | "require_approval" | "deny";
  daily_quota: number | null;
  quota_remaining: number | null;
}

export interface GuardrailVerdict {
  rule_id: string;
  verdict: "allow" | "require_approval" | "deny";
  reason: string;
  matched_pattern: string | null;
  quota_remaining: number | null;
  quota_exhausted: boolean;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  status: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CheckpointEntry {
  id: string;
  name: string;
  kind: string;
  target: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RollbackResult {
  git_ok: boolean;
  db_ok: boolean;
  config_ok: boolean;
  restored: string[];
  errors: string[];
}

export interface CapabilityEntry {
  name: string;
  enabled: boolean;
  updated_at: string;
}

// ---- B6 wave 4b: Skills ----

export interface SkillSummary {
  name: string;
  source: "builtin" | "db";
  version: string;
  enabled: boolean;
  description: string;
  tags: string[];
}

export interface SkillDetail extends SkillSummary {
  prompt: string;
  allowed_tools: string[] | null;
  forbidden_tools: string[];
}

export interface SkillEntryRow {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkillCurateBuckets {
  stale: string[];
  underused: string[];
  failing: string[];
  overlapping: { a: string; b: string; ratio: number }[];
}

export interface AgentProfile {
  id: string;
  name: string;
  role: string;
  prompt_id: string | null;
  prompt_label: string | null;
  toolset_id: string | null;
  toolset_label: string | null;
  provider_id: string | null;
  provider_label: string | null;
  budget: Record<string, unknown>;
  meta: Record<string, unknown>;
  reflection_enabled: boolean;
  reflection_max_retries: number;
  created_at: string;
  updated_at: string;
}

export interface TaskRun {
  id: string;
  task_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  trigger: string;
  resume_from_run_id: string | null;
  result: Record<string, unknown>;
  error: string | null;
  meta: Record<string, unknown>;
  started_at: string;
  finished_at: string | null;
}

export interface TaskEntry {
  id: string;
  goal: string;
  kind: "interactive" | "background" | "delegated" | "scheduled";
  status: "queued" | "running" | "paused" | "completed" | "failed" | "cancelled";
  agent_profile: string | null;
  parent_task_id: string | null;
  owner: string | null;
  meta: Record<string, unknown>;
  artifacts: string[];
  created_at: string;
  updated_at: string;
}

export interface TaskDetail extends TaskEntry {
  child_status_summary: Record<string, number>;
  runs: TaskRun[];
}

export interface JobRunRecord {
  id: string;
  job_name: string;
  task_id: string | null;
  status: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface ScheduledJob {
  name: string;
  goal: string;
  cron: string;
  enabled: boolean;
  agent_profile: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ScheduledJobDetail extends ScheduledJob {
  runs: JobRunRecord[];
}

export interface PromptBundleDetail extends PromptBundle {
  versions?: PromptVersion[];
}

export interface Toolset {
  id: string;
  name: string;
  description: string | null;
  members: string[];
  meta: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

export interface ToolsetPayload {
  name: string;
  description?: string | null;
  members?: string[];
  meta?: Record<string, unknown>;
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

// Phase B1:trace platform
export type TurnStatus = "running" | "completed" | "failed" | "cancelled";

export interface TraceTurn {
  id: string;
  conversation_id: string | null;
  agent_profile: string | null;
  task_id: string | null;
  task_run_id: string | null;
  provider_snapshot: string;
  model: string | null;
  prompt_trace_id: string | null;
  context_trace_id: string | null;
  status: TurnStatus;
  stop_reason: string | null;
  error_type: string | null;
  error_message: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  reasoning_tokens: number | null;
  cost_usd: number | null;
  cost_status: string | null;
  duration_ms: number | null;
  started_at: string;
  finished_at: string | null;
  meta: Record<string, unknown>;
}

export interface TraceProviderCall {
  id: string;
  turn_id: string;
  provider_snapshot: string;
  model: string | null;
  log_id: string | null;
  request_summary: Record<string, unknown>;
  response_summary: Record<string, unknown>;
  started_at: string;
  finished_at: string | null;
  latency_ms: number | null;
  error_type: string | null;
}

export interface TraceToolCall {
  id: string;
  turn_id: string;
  provider_call_id: string | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  duration_ms: number | null;
  status: "ok" | "error";
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface TraceCheckpoint {
  id: string;
  turn_id: string;
  kind: string;
  snapshot_id: string | null;
  created_at: string;
}

export interface TraceTree {
  turn: TraceTurn;
  provider_calls: TraceProviderCall[];
  tool_calls: TraceToolCall[];
  checkpoints: TraceCheckpoint[];
}

export interface ListTracesParams {
  conversation_id?: string;
  task_id?: string;
  provider_snapshot?: string;
  status?: TurnStatus;
  limit?: number;
  offset?: number;
}

// Phase B2 wave 5: eval surface
export type EvalVerdict = "PASS" | "FAIL" | "ERROR" | "SKIP";

export type DiffStatus = "NEW" | "REMOVED" | "REGRESSED" | "RECOVERED" | "CHANGED" | "STABLE";

export interface GoldenTaskEntry {
  task_id: string;
  prompt: string;
  verifier_type: string;
  expected: Record<string, unknown>;
  category: string;
  description: string;
  max_iterations: number;
  model: string | null;
  system: string | null;
}

export interface EvalRunRecord {
  task_id: string;
  verdict: EvalVerdict;
  reason: string;
  turn_id: string;
  turns: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  cost_status: string;
  duration_seconds: number;
  final_response: string;
  tool_calls: Array<Record<string, unknown>>;
  error: string | null;
}

export interface EvalRunSummary {
  total: number;
  passed: number;
  failed: number;
  errored: number;
  skipped: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  pass_rate: number;
}

export interface EvalRunMeta {
  run_id: string;
  created_at: string;
  schema: number;
  agent_profile?: string | null;
  golden_dir?: string | null;
  [key: string]: unknown;
}

export interface EvalRunListEntry {
  run_id: string;
  summary: EvalRunSummary | null;
  meta: EvalRunMeta | null;
}

export interface EvalRunSnapshot {
  run_id: string;
  run_dir: string;
  meta: EvalRunMeta;
  summary: EvalRunSummary;
  tasks: GoldenTaskEntry[];
  records: EvalRunRecord[];
}

export interface DiffEntry {
  task_id: string;
  status: DiffStatus;
  baseline_verdict: EvalVerdict | null;
  current_verdict: EvalVerdict | null;
}

export interface DiffSummaryPayload {
  new: number;
  removed: number;
  regressed: number;
  recovered: number;
  changed: number;
  stable: number;
  total_changes: number;
}

export interface DiffResult {
  baseline_id: string;
  current_id: string;
  entries: DiffEntry[];
  summary: DiffSummaryPayload;
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

  updateConversationConfig(
    conversation_id: string,
    config: { agent_profile?: string | null },
  ): Promise<{ conversation: Conversation }> {
    return rpc("update_conversation_config", { conversation_id, ...config });
  },

  deleteConversation(conversation_id: string): Promise<{ deleted: string }> {
    return rpc("delete_conversation", { conversation_id });
  },

  searchConversation(params: {
    query: string;
    limit?: number;
    conversation_id?: string;
  }): Promise<{ hits: ConversationSearchHit[] }> {
    return rpc("search_conversation", params);
  },

  rebuildConversationFts(): Promise<{ rebuilt: number }> {
    return rpc("rebuild_conversation_fts");
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

  setToolState(name: string, req: { enabled?: boolean; options?: Record<string, unknown> }): Promise<{ tool: Tool }> {
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

  createTool(req: {
    name: string;
    custom_type: string;
    options: Record<string, unknown>;
    description?: string;
  }): Promise<{ tool: Tool }> {
    return rpc("add_tool", req);
  },

  deleteTool(name: string): Promise<{ deleted: string }> {
    return rpc("delete_tool", { name });
  },

  updateTool(
    name: string,
    req: {
      options?: Record<string, unknown>;
      description?: string | null;
    },
  ): Promise<{ tool: Tool }> {
    return rpc("update_tool", { name, ...req });
  },

  listToolsets(): Promise<{ toolsets: Toolset[] }> {
    return rpc("list_toolsets");
  },

  getToolset(name: string): Promise<{ toolset: Toolset }> {
    return rpc("get_toolset", { name });
  },

  createToolset(req: ToolsetPayload): Promise<{ toolset: Toolset }> {
    return rpc("add_toolset", { ...req });
  },

  updateToolset(
    name: string,
    req: Omit<ToolsetPayload, "name"> & { rename?: string },
  ): Promise<{ toolset: Toolset }> {
    return rpc("update_toolset", { name, ...req });
  },

  deleteToolset(name: string): Promise<{ deleted: string }> {
    return rpc("delete_toolset", { name });
  },

  addToolsetMember(name: string, tool_name: string): Promise<{ toolset: Toolset }> {
    return rpc("add_toolset_member", { name, tool_name });
  },

  removeToolsetMember(name: string, tool_name: string): Promise<{ toolset: Toolset }> {
    return rpc("remove_toolset_member", { name, tool_name });
  },

  listProviders(): Promise<{ providers: Provider[] }> {
    return rpc("list_providers");
  },

  showProvider(ref: string): Promise<{ provider: Provider }> {
    return rpc("show_provider", { name: ref });
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
    ref: string,
    req: {
      rename?: string;
      type?: string;
      options?: Record<string, unknown>;
      params?: Record<string, unknown>;
    },
  ): Promise<{ provider: Provider }> {
    return rpc("update_provider", { name: ref, ...req });
  },

  deleteProvider(ref: string): Promise<{ deleted: string }> {
    return rpc("delete_provider", { name: ref });
  },

  useProvider(ref: string): Promise<{ provider: Provider }> {
    return rpc("use_provider", { name: ref });
  },

  async duplicateProvider(ref: string, as_?: string): Promise<{ provider: Provider }> {
    const { providers } = await apiCore.listProviders();
    const src = providers.find((p) => p.id === ref);
    if (!src) throw new RpcError(-32001, `provider ${ref} not found`);
    const newName = (as_ ?? `${src.name}_copy`).trim();
    const { provider } = await apiCore.addProvider({
      name: newName,
      type: src.type,
      options: src.options,
      params: src.params,
    });
    return { provider };
  },

  probeProvider(ref: string): Promise<ProbeResult> {
    return rpc("probe_provider", { name: ref });
  },

  getProviderStatus(): Promise<ProviderStatusResponse> {
    return rpc("get_provider_status");
  },

  listLogs(params: ListLogsParams = {}): Promise<{ logs: LogEntry[] }> {
    return rpc("list_logs", { ...params });
  },

  listTraces(params: ListTracesParams = {}): Promise<{ turns: TraceTurn[] }> {
    return rpc("list_traces", { ...params });
  },

  getTraceTurn(turn_id: string): Promise<{ turn: TraceTurn }> {
    return rpc("get_trace_turn", { turn_id });
  },

  viewTraceTree(turn_id: string): Promise<TraceTree> {
    return rpc("view_trace_tree", { turn_id });
  },

  reconcileTraces(older_than_seconds?: number): Promise<{ cleaned: number }> {
    return rpc("reconcile_traces", { older_than_seconds });
  },

  // Phase B2 wave 5: eval
  listGoldenTasks(golden_dir?: string): Promise<{ tasks: GoldenTaskEntry[]; golden_dir: string; missing: boolean }> {
    return rpc("list_golden_tasks", golden_dir ? { golden_dir } : {});
  },

  listEvalRuns(runs_dir?: string): Promise<{ runs: EvalRunListEntry[]; runs_dir: string }> {
    return rpc("list_eval_runs", runs_dir ? { runs_dir } : {});
  },

  getEvalRun(run_id: string, runs_dir?: string): Promise<EvalRunSnapshot> {
    return rpc("get_eval_run", runs_dir ? { run_id, runs_dir } : { run_id });
  },

  diffEvalRuns(baseline_id: string, current_id: string, runs_dir?: string): Promise<DiffResult> {
    const params: Record<string, string> = { baseline_id, current_id };
    if (runs_dir) params.runs_dir = runs_dir;
    return rpc("diff_eval_runs", params);
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

  listMemories(params: {
    kind?: string;
    pinned?: boolean;
    archived?: boolean;
    conversation_id?: string;
    provider_snapshot?: string;
    tag?: string;
    search?: string;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ entries: MemoryEntry[] }> {
    return rpc("list_memories", { ...params });
  },

  getMemory(memory_id: string): Promise<{ memory: MemoryEntry }> {
    return rpc("get_memory", { memory_id });
  },

  createMemory(payload: {
    kind: string;
    text: string;
    meta?: Record<string, unknown>;
    pinned?: boolean;
    archived?: boolean;
    links?: Array<Record<string, unknown>>;
  }): Promise<{ memory: MemoryEntry }> {
    return rpc("create_memory", payload as Record<string, unknown>);
  },

  updateMemory(
    memory_id: string,
    payload: {
      kind?: string;
      text?: string;
      meta?: Record<string, unknown>;
      pinned?: boolean;
      archived?: boolean;
      links?: Array<Record<string, unknown>>;
    },
  ): Promise<{ memory: MemoryEntry }> {
    return rpc("update_memory", { memory_id, ...payload });
  },

  deleteMemory(memory_id: string): Promise<{ deleted: string }> {
    return rpc("delete_memory", { memory_id });
  },

  pinMemory(memory_id: string): Promise<{ memory: MemoryEntry }> {
    return rpc("pin_memory", { memory_id });
  },

  archiveMemory(memory_id: string): Promise<{ memory: MemoryEntry }> {
    return rpc("archive_memory", { memory_id });
  },

  listMemoryEvents(memory_id?: string): Promise<{ events: MemoryEvent[] }> {
    return rpc("list_memory_events", memory_id ? { memory_id } : {});
  },

  listMemoryLinks(memory_id?: string): Promise<{ links: MemoryLink[] }> {
    return rpc("list_memory_links", memory_id ? { memory_id } : {});
  },

  searchMemory(query: string): Promise<{ entries: MemoryEntry[] }> {
    return rpc("search_memory", { query });
  },

  addPromptBundle(payload: PromptBundlePayload): Promise<{ bundle: PromptBundle; version: PromptVersion }> {
    return rpc("add_prompt_bundle", payload as unknown as Record<string, unknown>);
  },

  updatePromptBundle(
    payload: PromptBundlePayload & { rename?: string },
  ): Promise<{ bundle: PromptBundle; version: PromptVersion }> {
    return rpc("update_prompt_bundle", payload as unknown as Record<string, unknown>);
  },

  activatePromptBundle(payload: PromptActivatePayload): Promise<{ bundle: PromptBundle; version: PromptVersion | null }> {
    return rpc("activate_prompt_bundle", payload as unknown as Record<string, unknown>);
  },

  listAgents(): Promise<{ agents: AgentProfile[] }> {
    return rpc("list_agents");
  },

  getAgent(name: string): Promise<{ agent: AgentProfile }> {
    return rpc("get_agent", { name });
  },

  createAgent(payload: {
    name: string;
    role: string;
    prompt_id?: string | null;
    toolset_id?: string | null;
    provider_id?: string | null;
    budget?: Record<string, unknown>;
    meta?: Record<string, unknown>;
    reflection_enabled?: boolean;
    reflection_max_retries?: number;
  }): Promise<{ agent: AgentProfile }> {
    return rpc("create_agent", payload as Record<string, unknown>);
  },

  updateAgent(
    name: string,
    payload: {
      rename?: string;
      role?: string | null;
      prompt_id?: string | null;
      toolset_id?: string | null;
      provider_id?: string | null;
      budget?: Record<string, unknown>;
      meta?: Record<string, unknown>;
      reflection_enabled?: boolean;
      reflection_max_retries?: number;
    },
  ): Promise<{ agent: AgentProfile }> {
    return rpc("update_agent", { name, ...payload });
  },

  deleteAgent(name: string): Promise<{ deleted: string }> {
    return rpc("delete_agent", { name });
  },

  listTasks(params: { parent_task_id?: string | null } = {}): Promise<{ tasks: TaskEntry[] }> {
    return rpc("list_tasks", { ...params });
  },

  getTask(task_id: string): Promise<{ task: TaskDetail }> {
    return rpc("get_task", { task_id });
  },

  createTask(payload: {
    goal: string;
    kind?: TaskEntry["kind"];
    agent_profile?: string | null;
    owner?: string | null;
    meta?: Record<string, unknown>;
  }): Promise<{ task: TaskEntry }> {
    return rpc("create_task", payload as Record<string, unknown>);
  },

  pauseTask(task_id: string): Promise<{ task: TaskEntry }> {
    return rpc("pause_task", { task_id });
  },

  resumeTask(task_id: string): Promise<{ task: TaskEntry }> {
    return rpc("resume_task", { task_id });
  },

  cancelTask(task_id: string): Promise<{ task: TaskEntry }> {
    return rpc("cancel_task", { task_id });
  },

  startTaskRun(payload: {
    task_id: string;
    trigger?: string;
    resume_from_run_id?: string | null;
    meta?: Record<string, unknown>;
  }): Promise<{ run: TaskRun }> {
    return rpc("start_task_run", payload as Record<string, unknown>);
  },

  completeTaskRun(payload: {
    run_id: string;
    result?: Record<string, unknown>;
    error?: string | null;
  }): Promise<{ run: TaskRun }> {
    return rpc("complete_task_run", payload as Record<string, unknown>);
  },

  failTaskRun(payload: {
    run_id: string;
    error: string;
    result?: Record<string, unknown>;
  }): Promise<{ run: TaskRun }> {
    return rpc("fail_task_run", payload as Record<string, unknown>);
  },

  cancelTaskRun(payload: {
    run_id: string;
    error?: string | null;
    result?: Record<string, unknown>;
  }): Promise<{ run: TaskRun }> {
    return rpc("cancel_task_run", payload as Record<string, unknown>);
  },

  delegateTask(payload: {
    parent_task_id: string;
    tasks: Array<{
      goal: string;
      agent_profile?: string | null;
      owner?: string | null;
      meta?: Record<string, unknown>;
    }>;
    reason?: string | null;
    meta?: Record<string, unknown>;
  }): Promise<{
    delegation: {
      parent_task_id: string;
      child_task_ids: string[];
      requested: number;
      created: number;
    };
  }> {
    return rpc("delegate_task", payload as Record<string, unknown>);
  },

  listJobs(): Promise<{ jobs: ScheduledJob[] }> {
    return rpc("list_jobs");
  },

  showJob(name: string): Promise<{ job: ScheduledJobDetail }> {
    return rpc("show_job", { name });
  },

  createJob(payload: {
    name: string;
    goal: string;
    cron: string;
    enabled?: boolean;
    agent_profile?: string | null;
    meta?: Record<string, unknown>;
  }): Promise<{ job: ScheduledJob }> {
    return rpc("create_job", payload as Record<string, unknown>);
  },

  updateJob(
    name: string,
    payload: {
      goal?: string | null;
      cron?: string | null;
      agent_profile?: string | null;
      meta?: Record<string, unknown>;
    },
  ): Promise<{ job: ScheduledJob }> {
    return rpc("update_job", { name, ...payload });
  },

  enableJob(name: string): Promise<{ job: ScheduledJob }> {
    return rpc("enable_job", { name });
  },

  disableJob(name: string): Promise<{ job: ScheduledJob }> {
    return rpc("disable_job", { name });
  },

  runJobNow(name: string): Promise<{ task: TaskEntry; job_run: JobRunRecord }> {
    return rpc("run_job_now", { name });
  },

  deleteJob(name: string): Promise<{ deleted: string }> {
    return rpc("delete_job", { name });
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

  // ---- B5 wave 4: Guardrails / Audit / Checkpoints / Capabilities ----

  listGuardrails(): Promise<{ rules: GuardrailRule[] }> {
    return rpc("list_guardrails", {});
  },

  tryGuardrail(params: {
    tool_name: string;
    args: Record<string, unknown>;
  }): Promise<GuardrailVerdict> {
    return rpc("try_guardrail", params as Record<string, unknown>);
  },

  listAuditEvents(params: { limit?: number } = {}): Promise<{ events: AuditEvent[] }> {
    return rpc("list_audit_events", { ...params });
  },

  getAuditEvent(event_id: string): Promise<{ event: AuditEvent }> {
    return rpc("get_audit_event", { event_id });
  },

  listCheckpoints(): Promise<{ checkpoints: CheckpointEntry[] }> {
    return rpc("list_checkpoints", {});
  },

  getCheckpoint(checkpoint_id: string): Promise<{ checkpoint: CheckpointEntry }> {
    return rpc("get_checkpoint", { checkpoint_id });
  },

  createCheckpoint(name: string): Promise<{ checkpoint: CheckpointEntry }> {
    return rpc("create_checkpoint", { name });
  },

  rollbackCheckpoint(checkpoint_id: string): Promise<RollbackResult> {
    return rpc("rollback_checkpoint", { checkpoint_id });
  },

  deleteCheckpoint(checkpoint_id: string): Promise<{ deleted: string }> {
    return rpc("delete_checkpoint", { checkpoint_id });
  },

  listCapabilities(): Promise<{ capabilities: CapabilityEntry[] }> {
    return rpc("list_capabilities", {});
  },

  setCapability(name: string, enabled: boolean): Promise<{ capability: CapabilityEntry }> {
    return rpc("set_capability", { name, enabled });
  },

  // ---- B6 wave 4b: Skills ----

  listSkills(): Promise<{ skills: SkillSummary[] }> {
    return rpc("list_skills", {});
  },

  getSkill(name: string): Promise<{ skill: SkillDetail }> {
    return rpc("get_skill", { name });
  },

  installSkill(
    params: { content: string; enabled?: boolean } | { from_builtin: string; enabled?: boolean },
  ): Promise<{ skill: SkillEntryRow }> {
    return rpc("install_skill", params as Record<string, unknown>);
  },

  enableSkill(name: string): Promise<{ skill: SkillEntryRow }> {
    return rpc("enable_skill", { name });
  },

  disableSkill(name: string): Promise<{ skill: SkillEntryRow }> {
    return rpc("disable_skill", { name });
  },

  deleteSkill(name: string): Promise<{ deleted: string }> {
    return rpc("delete_skill", { name });
  },

  curateSkills(): Promise<SkillCurateBuckets> {
    return rpc("curate_skills", {});
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
