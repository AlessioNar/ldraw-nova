// Client for "Make this a template" (backend/routers/templates_api.py, docs/plans/active/build-templates section 6).
import { request } from "../api";

const base = (id: string) => `/api/builds/${encodeURIComponent(id)}/template`;
const post = <T>(url: string, body?: unknown) => request<T>(url, { method: "POST", body: JSON.stringify(body ?? {}) });

export type TemplateGate = { ok: boolean; reasons: string[]; parts: number; errors: number; components: number };
/** A part the classifier is unsure about; `section` is empty for the root section (what the canvas selects). */
export type UnsurePart = { section: string; id: string; key: string; role: string; confidence: number; part: string; name: string; colour: number };
export type RolePreview = {
  domain: string; roles: Record<string, { count: number; colours: Record<string, number> }>; low_confidence: UnsurePart[]; low_total: number;
  palette: Record<string, number>; vocabulary: string[]; corrected: number; url: string;
};
export type TemplateSource = { name: string; build: string; version: number | null; draft_changes?: number; author?: string; license?: string };
export type TemplateCheck = { gate: TemplateGate | null; source: TemplateSource; suggested_kinds: string[]; preview?: RolePreview };
export type Correction = { section: string; id: string; role: string };

export type Knob = {
  param: string; label: string; unit: string | null; default: unknown; options: (string | number)[] | null; boolean: boolean;
  description: string; extremes: { value: string | number | boolean; text: string; thumb?: string }[];
};
export type JobResult = {
  knobs: Knob[]; rejected: { param: string; reason: string }[]; roles: string[]; coverage: string[]; quality: string;
  title: string | null; kinds: string[]; description: string; source: TemplateSource;
};
export type JobRow = {
  job: string; state: "running" | "done" | "failed" | "cancelled"; error: string | null; elapsed: number | null;
  progress: { step: string; detail: string; elapsed: number } | null; result?: JobResult;
};
export type SavedTemplate = { id: string; generator: string; title: string; kinds: string[]; coverage: string[] };
export type StoredTemplate = { id: string; title: string | null; kinds: string[]; families: string[]; coverage: string[]; source: TemplateSource; parts: number; created_at: number };

export const templateApi = {
  check: (id: string, corrections: Correction[] = [], gate = true) => post<TemplateCheck>(`${base(id)}/check`, { corrections, gate }),
  start: (id: string, body: { title: string; kinds: string[]; corrections: Correction[] }) => post<{ job: string }>(`${base(id)}/jobs`, body),
  job: (id: string, job: string) => request<JobRow>(`${base(id)}/jobs/${job}`),
  cancel: (id: string, job: string) => post<{ state: string }>(`${base(id)}/jobs/${job}/cancel`),
  save: (id: string, job: string, body: { title: string; kinds: string[]; disabled: string[] }) => post<SavedTemplate>(`${base(id)}/jobs/${job}/save`, body),
  list: () => request<{ templates: StoredTemplate[] }>("/api/templates"),
  remove: (template: string) => request<{ deleted: string }>(`/api/templates/${encodeURIComponent(template)}`, { method: "DELETE" }),
};
