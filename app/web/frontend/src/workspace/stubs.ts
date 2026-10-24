// Stand-in for the W1 endpoints (CONTRACT.md) until the backend answers them. wsApi.ts switches to this only when
// GET /api/builds/{id}/workspace comes back 404/405, and never again once the real endpoint has answered.
// State lives in memory (a reload forgets it). Versions come from the real build; drafts, saves and proposals are fake.
import { buildsApi, type BuildDetail } from "../api";
import type { DescribeResult, DraftInfo, MissingLine, PartsCheckData, Proposal, RegenerateResult, WorkspaceData } from "./types";

type State = { base: number; baseRev: number; rev: number; undo: number; redo: number; open: boolean; extra: number[]; proposals: Proposal[]; seeded: boolean };
const states = new Map<string, State>();
const stateOf = (id: string): State => {
  let s = states.get(id);
  if (!s) {
    s = { base: 0, baseRev: 10, rev: 10, undo: 0, redo: 0, open: false, extra: [], proposals: [], seeded: false };
    states.set(id, s);
  }
  return s;
};
const conflict = (changes: number) => Object.assign(new Error(`The draft has ${changes} changes`), { status: 409, detail: { changes } });

const draftOf = (s: State): DraftInfo | null => (s.open
  ? { base_version: s.base, changes: s.rev - s.baseRev, revision: s.rev, undo_depth: s.undo, redo_depth: s.redo, updated_at: Date.now() / 1000 } : null);

const checkOf = (parts: number, version: number): PartsCheckData => {
  const missing = version % 2 === 0 && parts > 4 ? 2 : 0;
  return { owned: Math.max(parts - missing, 0), needed: parts, missing };
};

const latestOf = (d: BuildDetail, s: State) => [...d.versions.map((v) => v.version), ...s.extra].reduce((a, b) => Math.max(a, b), 0);

export const stub = {
  /** What the canvas stand-in calls for an edit, undo or redo. */
  edit(id: string) { const s = stateOf(id); s.rev += 1; s.undo += 1; s.redo = 0; return draftOf(s); },
  undo(id: string) { const s = stateOf(id); if (s.undo > 0 && s.rev > s.baseRev) { s.rev -= 1; s.undo -= 1; s.redo += 1; } return draftOf(s); },
  redo(id: string) { const s = stateOf(id); if (s.redo > 0) { s.rev += 1; s.redo -= 1; s.undo += 1; } return draftOf(s); },

  async workspace(id: string): Promise<WorkspaceData> {
    const d = await buildsApi.get(id), s = stateOf(id);
    const versions = d.versions.map((v) => ({ version: v.version, file: v.file, origin: v.origin, parts: v.parts ?? 0, parts_check: checkOf(v.parts ?? 0, v.version) }));
    for (const n of s.extra) {
      const parts = (versions.at(-1)?.parts ?? 0) + 2;
      versions.push({ version: n, file: `stub-v${n}.mpd`, origin: "editor", parts, parts_check: checkOf(parts, n) });
    }
    const chat = d.chats.find((c) => c.attached) ?? d.chats[0];
    if (!s.seeded && versions.length) {      // one demo proposal so the AI tab has a card to show
      s.seeded = true;
      s.proposals.push({ id: "stub-1", created_at: Date.now() / 1000 - 600, summary: { added: 14, removed: 0, moved: 0 }, cost_eur: 0.06, chat_id: chat?.id ?? null });
    }
    return {
      versions, draft: draftOf(s), proposals: s.proposals, chat_id: chat?.id ?? null,
      generator: d.source.generator ? { id: d.source.generator, params: d.source.params ?? {} } : null,
    };
  },
  async draftOpen(id: string, body: { version?: number; force?: boolean }) {
    const d = await buildsApi.get(id), s = stateOf(id);
    const want = body.version ?? (s.open ? s.base : latestOf(d, s));
    if (s.open && want !== s.base && s.rev > s.baseRev && !body.force) throw conflict(s.rev - s.baseRev);
    if (!s.open || want !== s.base) { s.base = want; s.baseRev = s.rev = 10; s.undo = s.redo = 0; }
    s.open = true;
    return { draft: draftOf(s) };
  },
  async draftSave(id: string) {
    const d = await buildsApi.get(id), s = stateOf(id);
    const next = latestOf(d, s) + 1;
    s.extra.push(next); s.base = next; s.baseRev = s.rev; s.undo = s.redo = 0;
    return { version: next, file: `stub-v${next}.mpd`, message: `Saved as v${next} (stub: nothing was written)` };
  },
  async draftDiscard(id: string) { const s = stateOf(id); s.rev = s.baseRev; s.undo = s.redo = 0; return { draft: draftOf(s) }; },
  async regenerate(id: string): Promise<RegenerateResult> {
    const s = stateOf(id), replaced = s.rev > s.baseRev;
    s.rev += 1; s.undo += 1; s.redo = 0;
    return { replaced_manual_edits: replaced, draft: draftOf(s) };
  },
  async describe(id: string, text: string): Promise<DescribeResult> {
    const d = await buildsApi.get(id), s = stateOf(id);
    const current = d.source.generator && d.source.params ? { generator: d.source.generator, params: d.source.params } : undefined;
    let answer;
    try { answer = await buildsApi.describe(text, id, current); }
    catch (e) {
      if ([404, 405].includes((e as { status?: number }).status ?? 0)) return { route: "needs_ai", reason: "The fast path is not installed here, so only the AI can do this." };
      throw e;
    }
    if (answer.route === "generator" || answer.route === "edit") { s.rev += 1; s.undo += 1; s.redo = 0; return { ...answer, draft: draftOf(s) }; }
    return answer;
  },
  async partsCheck(id: string): Promise<PartsCheckData> {
    const d = await buildsApi.get(id), s = stateOf(id);
    const base = d.versions.find((v) => v.version === s.base) ?? d.versions.at(-1);
    const parts = base?.parts ?? 0;
    const n = (checkOf(parts, s.base).missing as number) + (s.rev - s.baseRev > 2 ? 1 : 0);
    const lines: MissingLine[] = n ? [{ part: "3037", colour: "Blue", need: 20, have: 20 - Math.min(n, 2) }, ...(n > 2 ? [{ part: "3036", colour: "Blue", need: 2, have: 1 }] : [])] : [];
    return { owned: Math.max(parts - n, 0), needed: parts, missing: lines };
  },
  async acceptProposal(id: string, pid: string) {
    const s = stateOf(id); s.proposals = s.proposals.filter((p) => p.id !== pid);
    s.rev += 1; s.undo += 1; s.redo = 0; return { draft: draftOf(s) };
  },
  async discardProposal(id: string, pid: string) { const s = stateOf(id); s.proposals = s.proposals.filter((p) => p.id !== pid); return { ok: true }; },
};
