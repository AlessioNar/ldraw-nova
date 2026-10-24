// Client for the W1 endpoints. The first call (workspace) decides: a real answer pins "real" for the session, a 404/405
// pins "stub" (workspace/stubs.ts). Once real, nothing falls back, so integration changes no code.
import { request } from "../api";
import { stub } from "./stubs";
import type { DescribeResult, DraftInfo, PartsCheckData, RegenerateResult, WorkspaceData } from "./types";

let mode: "real" | "stub" | null = null;
export const usingStubs = () => mode === "stub";
const base = (id: string) => `/api/builds/${encodeURIComponent(id)}`;
const post = <T>(url: string, body?: unknown) => request<T>(url, { method: "POST", body: JSON.stringify(body ?? {}) });
const missing = (e: unknown) => [404, 405].includes((e as { status?: number }).status ?? 0);

/** The real endpoint unless the stub is pinned; the first call may pin it. */
async function route<T>(real: () => Promise<T>, fake: () => Promise<T>): Promise<T> {
  if (mode === "stub") return fake();
  try { const r = await real(); mode = "real"; return r; }
  catch (e) { if (mode === null && missing(e)) { mode = "stub"; return fake(); } throw e; }
}

export const wsApi = {
  workspace: (id: string) => route(() => request<WorkspaceData>(`${base(id)}/workspace`), () => stub.workspace(id)),
  open: (id: string, body: { version?: number; force?: boolean } = {}) =>
    route(() => post<{ draft: DraftInfo | null }>(`${base(id)}/draft/open`, body), () => stub.draftOpen(id, body)),
  save: (id: string) => route(() => post<{ version: number; file: string; message?: string }>(`${base(id)}/draft/save`), () => stub.draftSave(id)),
  discard: (id: string) => route(() => post<{ draft?: DraftInfo | null }>(`${base(id)}/draft/discard`), () => stub.draftDiscard(id)),
  regenerate: (id: string, generator: string, params: Record<string, unknown>) =>
    route(() => post<RegenerateResult>(`${base(id)}/draft/regenerate`, { generator, params }), () => stub.regenerate(id)),
  describe: (id: string, text: string) => route(() => post<DescribeResult>(`${base(id)}/draft/describe`, { text }), () => stub.describe(id, text)),
  partsCheck: (id: string) => route(() => request<PartsCheckData>(`${base(id)}/draft/parts-check`), () => stub.partsCheck(id)),
  accept: (id: string, pid: string) => route(() => post<{ draft?: DraftInfo | null }>(`${base(id)}/proposals/${encodeURIComponent(pid)}/accept`), () => stub.acceptProposal(id, pid)),
  discardProposal: (id: string, pid: string) => route(() => post<unknown>(`${base(id)}/proposals/${encodeURIComponent(pid)}/discard`), () => stub.discardProposal(id, pid)),
  proposalUrl: (id: string, pid: string) => `${base(id)}/proposals/${encodeURIComponent(pid)}.mpd`,
};
