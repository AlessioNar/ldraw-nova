// Client for the build workspace endpoints (docs/plans/build-workspace/CONTRACT.md).
import { request } from "../api";
import type { DescribeResult, DraftInfo, PartsCheckData, RegenerateResult, WorkspaceData } from "./types";

const base = (id: string) => `/api/builds/${encodeURIComponent(id)}`;
const post = <T>(url: string, body?: unknown) => request<T>(url, { method: "POST", body: JSON.stringify(body ?? {}) });

export const wsApi = {
  workspace: (id: string) => request<WorkspaceData>(`${base(id)}/workspace`),
  open: (id: string, body: { version?: number; force?: boolean } = {}) => post<{ draft: DraftInfo | null }>(`${base(id)}/draft/open`, body),
  save: (id: string) => post<{ version: number; file: string; message?: string }>(`${base(id)}/draft/save`),
  discard: (id: string) => post<{ draft?: DraftInfo | null }>(`${base(id)}/draft/discard`),
  regenerate: (id: string, generator: string, params: Record<string, unknown>) => post<RegenerateResult>(`${base(id)}/draft/regenerate`, { generator, params }),
  describe: (id: string, text: string) => post<DescribeResult>(`${base(id)}/draft/describe`, { text }),
  partsCheck: (id: string) => request<PartsCheckData>(`${base(id)}/draft/parts-check`),
  accept: (id: string, pid: string) => post<{ draft?: DraftInfo | null }>(`${base(id)}/proposals/${encodeURIComponent(pid)}/accept`),
  discardProposal: (id: string, pid: string) => post<unknown>(`${base(id)}/proposals/${encodeURIComponent(pid)}/discard`),
  proposalUrl: (id: string, pid: string) => `${base(id)}/proposals/${encodeURIComponent(pid)}.mpd`,
};
