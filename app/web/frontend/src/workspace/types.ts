// Shapes of docs/plans/build-workspace/CONTRACT.md: the W1 endpoints and the W2 canvas messages.
import type { BuildOrigin, Describe } from "../api";

export type MissingLine = { part: string; colour: number | string; need: number; have: number };
/** `missing` is a count in a version's check and a list in the draft's. */
export type PartsCheckData = { owned: number; needed: number; missing: number | MissingLine[] };
export const missingCount = (c: PartsCheckData | null | undefined): number =>
  c ? (Array.isArray(c.missing) ? c.missing.reduce((n, l) => n + Math.max(l.need - l.have, 0), 0) : c.missing) : 0;

export type DraftInfo = { base_version: number; changes: number; revision: number; undo_depth: number; redo_depth: number; updated_at: number };
export type WsVersion = { version: number; file: string; origin: BuildOrigin; parts: number; changes?: unknown; parts_check?: PartsCheckData | null; check?: { status: "checking" | "ok" | "warnings" | "failed"; warnings: string[] } | null };
export type Proposal = { id: string; created_at: number; summary: { added: number; removed: number; moved: number }; cost_eur: number | null; chat_id: string | null };
export type WorkspaceData = {
  build?: unknown; versions: WsVersion[]; draft: DraftInfo | null;
  generator: { id: string; params: Record<string, unknown> } | null; proposals: Proposal[]; chat_id: string | null;
  chat_mode?: "ai" | "quick"; agent_chat?: boolean;
};
export type DescribeResult = Partial<Describe> & { route: Describe["route"]; draft?: DraftInfo | null; chat_id?: string; took_ms?: number; choices?: string[]; chat_mode?: "ai" | "quick"; highlight?: string[] };
export type RegenerateResult = { replaced_manual_edits: boolean; draft?: DraftInfo | null };

// ---- canvas messages (window.postMessage, type prefixed "obd:") ----
export type CanvasMode = "edit" | "view" | "player";
export type Tool = "select" | "move" | "rotate" | "add" | "paint" | "delete";
export type CanvasPiece = { id: string; part: string; name?: string; colour: number | string; position?: number[]; rotation?: number[] };
export type CanvasEvent =
  | { type: "obd:ready"; mode: CanvasMode }
  | { type: "obd:selection"; pieces: CanvasPiece[] }
  | { type: "obd:changed"; revision: number; undo_depth: number; redo_depth: number }
  | { type: "obd:error"; message: string }
  | { type: "obd:mode"; mode: CanvasMode }
  | { type: "obd:tool"; tool: Tool }
  | { type: "obd:steps"; total: number; current: number }
  | { type: "obd:shortcut"; action: "save" };
export type CanvasCommand =
  | { cmd: "undo" } | { cmd: "redo" } | { cmd: "delete" } | { cmd: "reload" } | { cmd: "focus" } | { cmd: "back-to-draft" }
  | { cmd: "set-tool"; tool: Tool } | { cmd: "set-colour"; colour: number } | { cmd: "add-part"; part: string; colour: number }
  | { cmd: "set-mode"; mode: CanvasMode } | { cmd: "view-version"; url: string } | { cmd: "step"; n: number };
