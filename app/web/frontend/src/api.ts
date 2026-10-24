// Typed client for the FastAPI backend (web/backend/main.py).

export type SnapshotStatus = "ready" | "queued" | "rendering" | "failed" | "missing";

/** A model file in data/generated. */
export type ModelFile = {
  file: string;
  name: string;
  description: string; // the model's title line (line 2 of an .mpd)
  model_url: string | null; // null if the file is gone
  image_url: string | null; // the sibling .png, once it exists
  bom_url: string | null; // the sibling .csv bill of materials, once it exists
  parts: number | null; // total parts, from the BOM
  info_url: string | null; // the sibling .md: notes about the model (its prompt, say), if any
  info_heading: string | null; // the first H2 in the sibling .md, without the heading markers
  size: number;
  mtime: number;
  status: SnapshotStatus; // of the snapshot
  error: string | null;
  bom_status: SnapshotStatus;
  bom_error: string | null;
  chats?: { id: string; title: string }[]; // chats that produced it (My Builds page)
  origin?: string | null; // "editor": saved from the 3D editor
  build?: string; // the build this file is a version of (My Builds page)
};

/** A model a chat produced: a reference into data/generated. */
export type ModelChanges = {
  delta: number; total: number; summary: string;
  added: { part: string; name: string; colour: string; qty: number }[];
  removed: { part: string; name: string; colour: string; qty: number }[];
  recoloured: { part: string; name: string; from: string; to: string; qty: number }[];
};
export type ChatModel = ModelFile & {
  id: string; warnings: string[]; created_at: number;
  version?: number; latest?: boolean; title?: string; // per-chat version (v1, v2, ...) and a human title
  origin?: string | null; // "editor": published by the person from the 3D editor
  changes?: ModelChanges | null; // from the previous version's BOM
};

export type Chat = {
  id: string;
  title: string;
  llm_model_id: string | null;
  created_at: number;
  updated_at: number;
  running?: boolean;
  turn_steps?: number;   // model steps of the latest turn (list and detail)
  duration_s?: number | null; // wall-clock length of the latest turn
  options?: TurnOptions;
  models?: ChatModel[];
  build_id?: string; // set when the chat is attached to a build
};

export type ToolCall = { id: string; type: "function"; function: { name: string; arguments: string } };

export type Message = {
  id: number;
  created_at: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string | null | unknown[];
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
  _models?: string[];
  _usage?: Usage;
  _worker?: { id: string; model: string; steps: number; status: string }; // on a delegate result: the worker's aggregate
  _image_urls?: string[];
  _documents?: { name: string; size: number; url: string | null }[];
  _hidden?: boolean;
  _ui_only?: boolean;
  _label?: string; // text shown for an app-recorded user message (the editor publish) instead of its content
  _editor_publish?: boolean; // the person published from the 3D editor; its card follows this message
  _error?: boolean;
  _notice?: boolean;
  _budget_reached?: { spent_eur: number; limit_eur: number }; // the turn stopped at the per-request budget
  _reasoning?: string;
  parts_check?: PartsCheck; // set by the backend once a model is published under a parts steering mode
};

export type Usage = { prompt: number; completion: number; cache_read: number; cache_write: number; cost_usd?: number };
export type UsageTotals = Usage & { steps: number; cost_usd: number; cost_eur: number; eur_per_usd: number; unpriced_steps: number; worker_cost_eur?: number; turn_steps?: number; request_cost_eur?: number; duration_s?: number | null };
export type ChatBuildLink = { id: string; title: string; attached: boolean; latest: (ModelFile & { version: number }) | null };
export type ChatDetail = { chat: Chat; messages: Message[]; models: Record<string, ChatModel>; usage?: UsageTotals; build?: ChatBuildLink | null };

type Capability = boolean | "auto";
export type EnvironmentVariable = { id: string; name: string; value: null; has_value: boolean; fixed: boolean };
export type SandboxIsolation = { state: "on" | "off" | "n/a"; detail: string };
export type DatabaseStatus = { state: "ok" | "unavailable" | "off"; detail: string };
export type EnvironmentState = { variables: EnvironmentVariable[]; jev_source?: "typesafe" | "jev_openrouter" | "openrouter" | "none"; sandbox_isolation?: SandboxIsolation; database?: DatabaseStatus };
export type EnvironmentUpdate = { id?: string; name: string; value: string | null };
export type DocumentUpload = { name: string; data: string };
export type ConnectionStatus = "not_tested" | "connected" | "not_connected";
export type TurnOptions = { mode: "plan" | "agent"; permissions: "ask" | "full" | "read_only"; worker_model_id?: string | null; effort?: string | null; context_tokens?: number | null; budget_eur?: number | null; parts_mode?: PartsMode; inventory_ids?: string[] };
export type ModelProfile = {
  model: string; name: string; context_window: number | null; efforts: string[]; default_effort: string | null; context_budgets: number[];
  max_output_tokens?: number | null; tools?: boolean | null; vision?: boolean | null; reasoning?: boolean | null;
  pricing?: { input: number | null; output: number | null; currency: string; note?: string; tiers?: { over_prompt_tokens: number; input: number; output: number }[] } | null;
  source_url?: string | null; source_label?: string | null; verified_at?: string | null;
  lookup_status?: "live" | "published" | "unavailable"; recommendation?: string | null;
};
export type WorkerContract = { error?: string; role?: string; task?: string; tools?: string[]; inputs?: string[]; outputs?: string[];
  max_steps?: number; max_eur?: number; model?: string; write_folder?: string };
export type Approval = { id: string; call_id: string; name: string; arguments: string; contract?: WorkerContract };
/** A delegated worker as the live stream reports it (worker_start / worker_step / worker_end events). */
export type LiveWorker = { id: string; role: string; task: string; model: string; max_steps: number; max_eur: number;
  status: string; steps: number; eur: number };
export type WorkerTranscript = { id: string; messages: { role: string; content: unknown; tool_calls?: ToolCall[]; name?: string }[];
  files: { name: string; size: number; url: string }[] };
export type LlmEntry = {
  connection_status: ConnectionStatus;
  id: string;
  model_name: string;
  litellm_params: Record<string, unknown>;
  capabilities: { tools: Capability; vision: Capability };
  resolved_capabilities: { tools: boolean | null; vision: boolean | null };
  auth_mode: "api_key" | "browser";
  unsupported?: boolean;
  profile: ModelProfile;
};

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      const d = body.detail;
      detail = typeof d === "string" ? d : typeof d?.detail === "string" ? d.detail : JSON.stringify(d);
    } catch {
      /* not JSON */
    }
    throw Object.assign(new Error(detail || `HTTP ${res.status}`), { status: res.status });
  }
  const type = res.headers.get("content-type") || "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export type ProviderInfo = { provider: string; label: string; effort: boolean; prompt_caching: boolean; discovery: boolean };

export const api = {
  environment: () => request<EnvironmentState>("/api/environment"),
  checkEnvironment: (name: string, id?: string) => request<{ preconfigured: boolean; saved: boolean }>(`/api/environment/check?name=${encodeURIComponent(name)}&exclude_id=${encodeURIComponent(id ?? "")}`),
  saveEnvironment: (variables: EnvironmentUpdate[]) => request<{ variables: EnvironmentVariable[] }>("/api/environment", { method: "PUT", body: json({ variables }) }),
  chats: () => request<{ chats: Chat[] }>("/api/chats"),
  createChat: (llm_model_id?: string | null) =>
    request<Chat>("/api/chats", { method: "POST", body: json({ llm_model_id }) }),
  chat: (id: string) => request<ChatDetail>(`/api/chats/${id}`),
  renameChat: (id: string, title: string) =>
    request<Chat>(`/api/chats/${id}`, { method: "PATCH", body: json({ title }) }),
  deleteChat: (id: string) => request(`/api/chats/${id}`, { method: "DELETE" }),
  send: (id: string, text: string, llm_model_id?: string | null, options?: TurnOptions, images?: string[], documents?: DocumentUpload[]) =>
    request(`/api/chats/${id}/messages`, { method: "POST", body: json({ text, llm_model_id, options, images, documents }) }),
  approve: (id: string, approval: string, approved: boolean) => request(`/api/chats/${id}/approvals/${approval}`, { method: "POST", body: json({ approved }) }),
  catalog: () => request<{ models: ModelProfile[] }>("/api/model-catalog"),
  worker: (id: string, worker: string) => request<WorkerTranscript>(`/api/chats/${id}/workers/${worker}`),
  cancel: (id: string) => request(`/api/chats/${id}/cancel`, { method: "POST" }),
  models: () => request<{ models: ModelFile[]; pending: number }>("/api/models"),
  editModel: (filename: string) => request<{ build: string; file: string; editor_url: string; warnings: string[] }>(`/api/models/${encodeURIComponent(filename)}/edit`, { method: "POST" }),
  deleteModel: (filename: string) => request<{ deleted: string[] }>(`/api/models/${encodeURIComponent(filename)}`, { method: "DELETE" }),

  llmModels: () => request<{ models: LlmEntry[]; default_id: string | null; worker_id?: string | null; understanding_id?: string | null }>("/api/llm-models"),
  setWorkerDefault: (id: string | null) => request<{ worker_id: string | null }>("/api/llm-workers/default", { method: "PUT", body: json({ id }) }),
  setUnderstandingDefault: (id: string | null) => request<{ understanding_id: string | null }>("/api/llm-understanding/default", { method: "PUT", body: json({ id }) }),
  editLlm: (id: string) => request<LlmEntry>(`/api/llm-models/${id}/edit`),
  createLlm: (entry: Partial<LlmEntry>) => request<LlmEntry>("/api/llm-models", { method: "POST", body: json(entry) }),
  updateLlm: (id: string, entry: Partial<LlmEntry>) =>
    request<LlmEntry>(`/api/llm-models/${id}`, { method: "PUT", body: json(entry) }),
  deleteLlm: (id: string) => request(`/api/llm-models/${id}`, { method: "DELETE" }),
  defaultLlm: (id: string) => request(`/api/llm-models/${id}/default`, { method: "POST" }),
  testLlm: (id: string) =>
    request<{ ok: boolean; reply?: string; error?: string; connection_status: ConnectionStatus; profile: ModelProfile; capabilities?: LlmEntry["resolved_capabilities"] }>(
      `/api/llm-models/${id}/test`,
      { method: "POST" },
    ),
  importLlm: (yaml: string) =>
    request<{ imported: LlmEntry[] }>("/api/llm-models/import", { method: "POST", body: json({ yaml }) }),
  providerKeys: () => request<{ keys: Record<string, boolean>; rejected: string[] }>("/api/llm-providers/keys"),
  testProvider: (p: string) => request<{ ok: boolean; status?: string; message?: string; error?: string }>(`/api/llm-providers/${encodeURIComponent(p)}/test`, { method: "POST" }),
  providers: () => request<{ providers: string[]; catalogue: ProviderInfo[] }>("/api/llm-providers"),
  providerModels: (p: string) => request<{ models: string[] }>(`/api/llm-providers/${encodeURIComponent(p)}/models`),
};

export const downloadUrl = (url: string) => url + (url.includes("?") ? "&" : "?") + "download=1";

/** Download the sibling GLB, or convert and cache one when none exists. */
export async function downloadGlb(modelUrl: string, fileName: string): Promise<void> {
  const res = await fetch(`/api/glb?url=${encodeURIComponent(modelUrl)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : res.statusText);
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await res.blob());
  link.download = fileName.replace(/\.[^.]+$/, "") + ".glb";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 30000);
}

/** The 3D viewer (three.js), or the 3D player that animates the build (Rust/WebAssembly). */
export type ViewerMode = "viewer" | "player";

export const viewerUrl = (modelUrl: string, parts?: number | null, mode: ViewerMode = "viewer") =>
  `/viewer/${mode}.html?model=${encodeURIComponent(modelUrl)}` + (parts != null ? `&parts=${parts}` : "");

export const editorUrl = (chatId: string) => `/viewer/editor.html?chat=${encodeURIComponent(chatId)}`;
export const buildEditorUrl = (build: string) => `/viewer/editor.html?build=${encodeURIComponent(build)}`;

/** The editor's API base for a workspace: a chat id, or "b-<build>" for a build in My Builds. */
export const workspaceBase = (id: string) =>
  id.startsWith("b-") ? `/api/builds/${encodeURIComponent(id.slice(2))}` : `/api/chats/${encodeURIComponent(id)}`;

export const partsLabel = (parts: number) => `${parts.toLocaleString()} part${parts === 1 ? "" : "s"}`;

export const isPending = (m: { status: SnapshotStatus; bom_status: SnapshotStatus }) =>
  m.status === "queued" || m.status === "rendering" || m.bom_status === "queued" || m.bom_status === "rendering";

export function timeAgo(seconds: number): string {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

// --- My Parts: the user's own inventories (Rebrickable CSV) and the agent steering built on them ---

export type PartsMode = "off" | "prefer" | "only";

export type Inventory = {
  id: string; name: string; created_at: number; source_filename: string | null;
  distinct_parts: number; distinct_colours: number; total_pieces: number; spare_pieces: number;
  unmapped_parts: number; unmapped_colours: number;
  review_items?: number; review_pieces?: number; // colour-family pieces waiting for a colour; not in total_pieces
};
type BuildableLine = { part: string; colour: number; colour_name: string; description: string; needed: number; owned: number; missing: number };
export type BuildableModel = {
  file: string; id: string; set_id: string | null; description: string; pieces: number; matched: number; completeness: number;
  missing: number; missing_lines: number; custom_pieces: number; top_missing: BuildableLine[];
  substituted_matched?: number; substituted_completeness?: number; // only with substitute=true; never mixed into completeness
};
export type BuildableStatus = { state: "idle" | "building" | "ready"; done: number; total: number; error?: string };
export type BuildableReport = {
  inventories: string[]; inventory_pieces: number; unmapped_pieces: number; unmapped_share: number; substitute_colours: boolean;
  counts: { models: number; parsed: number; skipped: number; too_small: number; subvariants: number; ranked: number };
  warnings: string[]; results: BuildableModel[]; status: BuildableStatus;
};
export type BuildableQuery = { ids: string[]; limit?: number; min_parts?: number; max_missing_share?: number | null; query?: string; substitute?: boolean };
/** A Brickit Pileometer import: pile photos with colour families, mapped to default LDraw colours. */
export type PileometerSummary = {
  scans: number; rows: number; pieces: number; matched_rows: number;
  assigned_pieces: number; review_pieces: number;
  families: { family: string; colour: number | null; colour_name: string; candidates: { colour: number; name: string }[]; rows: number; pieces: number }[];
};
export type ImportReport = {
  dialect: string; total_rows: number; total_pieces: number; spare_pieces: number;
  unmapped_parts: { rb_part: string; colour: number; qty: number; name?: string; family?: string }[];
  unmapped_colours: { rb_colour: number; part: string; qty: number; family?: string }[];
  warnings: string[];
  pileometer?: PileometerSummary;
};
type InventoryRow = { part: string; colour: number; colour_name: string; qty: number; description: string };
export type InventoryDetail = {
  inventory: Inventory; rows: InventoryRow[]; total: number;
  top_colours: { colour: number; colour_name: string; qty: number }[];
};
/** What the backend found missing from the inventories once a model was published. */
export type PartsCheck = {
  mode: PartsMode; complete: boolean; needed_total: number; owned_total: number; summary: string;
  short: { part: string; colour: number; colour_name: string; needed: number; owned: number; missing: number }[];
};

export const inventoryApi = {
  list: () => request<{ inventories: Inventory[] }>("/api/inventories"),
  create: (name: string, csv: string, include_spares?: boolean) =>
    request<{ inventory: Inventory; report: ImportReport }>("/api/inventories", { method: "POST", body: json({ name, csv, include_spares }) }),
  /** Pileometer: assign colour families ({family: LDraw colour}); the rest stay in the review list. Re-reads the stored file. */
  assignFamilies: (id: string, family_colours: Record<string, number>) =>
    request<{ inventory: Inventory; report: ImportReport }>(`/api/inventories/${encodeURIComponent(id)}/family-colours`, { method: "PUT", body: json({ family_colours }) }),
  detail: (id: string, q: { query?: string; colour?: number | null; limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (q.query) params.set("query", q.query);
    if (q.colour != null) params.set("colour", String(q.colour));
    if (q.limit != null) params.set("limit", String(q.limit));
    if (q.offset) params.set("offset", String(q.offset));
    return request<InventoryDetail>(`/api/inventories/${encodeURIComponent(id)}?${params}`);
  },
  rename: (id: string, name: string) =>
    request<{ inventory: Inventory }>(`/api/inventories/${encodeURIComponent(id)}`, { method: "PATCH", body: json({ name }) }),
  remove: (id: string) => request<{ ok: true }>(`/api/inventories/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** The ranked list, or the cache build's progress (HTTP 202) while the bundled models' parts lists are still being scanned. */
  buildable: (q: BuildableQuery) => {
    const params = new URLSearchParams({ ids: q.ids.join(",") });
    if (q.limit != null) params.set("limit", String(q.limit));
    if (q.min_parts != null) params.set("min_parts", String(q.min_parts));
    if (q.max_missing_share != null) params.set("max_missing_share", String(q.max_missing_share));
    if (q.query) params.set("query", q.query);
    if (q.substitute) params.set("substitute", "true");
    return request<BuildableReport | BuildableStatus>(`/api/inventories/buildable?${params}`);
  },
  buildableStatus: () => request<BuildableStatus>("/api/inventories/buildable/status"),
  buildableWarm: () => request<BuildableStatus>("/api/inventories/buildable/warm", { method: "POST" }),
};

/** A bundled set model (data/models-annotated) as the 3D viewer can load it. */
export const referenceModelUrl = (file: string) => `/reference-models/${encodeURIComponent(file)}`;

// ---- Manual editor palette (task 04) -------------------------------------------------------------
export type EditorColour = { code: number; name: string; hex: string };
export type PaletteColourRow = { colour: number; name: string; owned: number; used: number; available: number };
export type PaletteItem = {
  part: string; ref: string; description: string; colours: PaletteColourRow[]; in_inventory: boolean;
  owned_total: number | null; available_total: number | null; used_up: boolean;
};
export type PaletteResult = { mode: PartsMode; items: PaletteItem[]; more: boolean };
export type PaletteCheck = {
  allowed: boolean; warning: string | null; message: string | null;
  owned: number | null; used: number; available: number | null; short?: [string, number, number, number][];
};

export const editorApi = {
  edits: (chatId: string, edits: Record<string, unknown>[], baseRevision: number) =>
    request<{ revision: number }>(`${workspaceBase(chatId)}/editor/edits`,
      { method: "POST", body: json({ edits, base_revision: baseRevision }) }),
};

export const paletteApi = {
  colours: () => request<{ colours: EditorColour[] }>("/api/editor/colours"),
  search: (chatId: string, q: { query?: string; colour?: number | null; inventoryOnly?: boolean; limit?: number }) => {
    const params = new URLSearchParams();
    if (q.query) params.set("query", q.query);
    if (q.colour != null) params.set("colour", String(q.colour));
    if (q.inventoryOnly) params.set("inventory_only", "true");
    if (q.limit != null) params.set("limit", String(q.limit));
    return request<PaletteResult>(`${workspaceBase(chatId)}/editor/palette?${params}`);
  },
  check: (chatId: string, part: string, colour: number, quantity = 1) =>
    request<PaletteCheck>(`${workspaceBase(chatId)}/editor/palette/check`, { method: "POST", body: json({ part, colour, quantity }) }),
};

// ---- Builds: a model project with versions; chats, the 3D editor and generators are ways to work on it ----------
export type BuildOrigin = "generator" | "ai" | "editor" | "import";
export type BuildSource = { kind: BuildOrigin | "empty"; generator?: string; params?: Record<string, unknown>; filename?: string };
export type BuildVersion = ModelFile & {
  version: number; origin: BuildOrigin; title: string; created_at: number;
  chat: { id: string; title: string } | null;
  changes: ModelChanges | null; // from the previous version's parts list
};
export type BuildSummary = {
  id: string; key: string; title: string; created_at: number; updated_at: number; source: BuildSource;
  versions: number; latest: (ModelFile & { version: number }) | null; latest_origin?: BuildOrigin | null; chats: number; pending: boolean;
};
export type BuildDetail = {
  build: BuildSummary; versions: BuildVersion[]; source: BuildSource; pending: boolean;
  chats: { id: string; title: string; updated_at: number; running: boolean; attached: boolean }[];
  settings_open: boolean; latest_from_generator: boolean;
};
export type OlderChat = { id: string; title: string; updated_at: number; running: boolean };

/** One parameter of a generator, as JSON schema (ldraw_tools/generators/base.py). */
export type ParamSpec = {
  type?: string | string[]; enum?: (string | number)[]; minimum?: number; maximum?: number; default?: unknown;
  description?: string; "x-colour"?: boolean; "x-even"?: boolean; uniqueItems?: boolean; items?: { enum?: string[] };
};
export type GeneratorEntry = {
  id: string; title: string; families: string[]; kinds: string[]; description: string; roles: string[];
  params: { properties: Record<string, ParamSpec> }; defaults: Record<string, unknown>;
};
export type GeneratorList = { generators: GeneratorEntry[]; colours: { name: string; code: number }[] };

/** What /api/describe (the fast path's decision engine, docs/plans/phase2/fast-path.md) answers. A 404 means the
 *  engine is not installed: the Describe box then offers only the AI. */
export type DescribeEstimate = { eur_low?: number; eur_high?: number; minutes_low?: number; minutes_high?: number; basis?: string };
export type Describe = {
  route: "generator" | "edit" | "needs_ai" | "clarify";
  generator?: string; title?: string; params?: Record<string, unknown>; notes?: string[];
  changes?: { param: string; label: string; from: unknown; to: unknown }[]; history?: "undo" | "revert";
  question?: string; reason?: string; estimate?: DescribeEstimate; understanding_cost_eur?: number | null;
};
export type DescribeHint = { route: Describe["route"]; hint: string; confidence: number; estimate?: DescribeEstimate };

const buildBase = (id: string) => `/api/builds/${encodeURIComponent(id)}`;
export const buildsApi = {
  list: () => request<{ builds: BuildSummary[]; older_chats: OlderChat[]; pending: number }>("/api/builds"),
  create: (title: string, source?: BuildSource) => request<BuildDetail & { id: string }>("/api/builds", { method: "POST", body: json({ title, source }) }),
  get: (id: string) => request<BuildDetail>(buildBase(id)),
  rename: (id: string, title: string) => request<BuildDetail>(buildBase(id), { method: "PATCH", body: json({ title }) }),
  generators: () => request<GeneratorList>("/api/generators"),
  preview: (generator: string, params: Record<string, unknown>) =>
    request<{ model_url: string }>("/api/generators/preview", { method: "POST", body: json({ generator, params }) }),
  generate: (id: string, generator: string, params: Record<string, unknown>) =>
    request<{ version: number; file: string; notes: string[] }>(`${buildBase(id)}/generate`, { method: "POST", body: json({ generator, params }) }),
  importFile: (id: string, filename: string, content: string) =>
    request<{ version: number; file: string }>(`${buildBase(id)}/import`, { method: "POST", body: json({ filename, content }) }),
  edit: (id: string) => request<{ editor_url: string }>(`${buildBase(id)}/edit`, { method: "POST" }),
  remove: (id: string) => request<{ deleted: string[]; chats_kept: string[] }>(buildBase(id), { method: "DELETE" }),
  chat: (id: string, llm_model_id?: string | null, isNew = false) =>
    request<Chat>(`${buildBase(id)}/chat`, { method: "POST", body: json({ llm_model_id, new: isNew }) }),
  describe: (text: string, build_id?: string, current?: { generator: string; params: Record<string, unknown> }) =>
    request<Describe>("/api/describe", { method: "POST", body: json({ text, build_id, current }) }),
  hint: (text: string, generator?: string) =>
    request<DescribeHint>(`/api/describe/estimate?text=${encodeURIComponent(text)}${generator ? `&generator=${encodeURIComponent(generator)}` : ""}`),
};

/** "about €1–2" from the estimate a needs_ai answer carries. */
export function estimateLabel(e: DescribeEstimate | undefined): string | null {
  const low = e?.eur_low, high = e?.eur_high;
  if (low == null && high == null) return null;
  const fmt = (n: number) => n.toFixed(2).replace(/\.?0+$/, "");
  if (low == null || high == null || low === high) return `about €${fmt((low ?? high)!)}`;
  return `about €${fmt(low)}–${fmt(high)}`;
}
export const ORIGIN_LABEL: Record<BuildOrigin, string> = { generator: "Generator", ai: "AI", editor: "Edited by you", import: "Imported" };
