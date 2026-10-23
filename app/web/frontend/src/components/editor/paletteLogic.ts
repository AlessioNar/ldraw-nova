import type { PaletteColourRow, PartsMode } from "../../api";

export type UsedRow = { part: string; colour: number; used: number };
export type PlanLike = { sections: { name: string; steps: { id: string }[][] }[] };

export const partKey = (part: string) => part.trim().toLowerCase().replace(/\.dat$/, "");

/** Pieces of (part, colour) the model uses right now; the editor view's live parts rows. */
export function usedNow(rows: UsedRow[], part: string, colour: number): number {
  const key = partKey(part);
  return rows.reduce((sum, r) => (partKey(r.part) === key && r.colour === colour ? sum + r.used : sum), 0);
}

/** Owned minus used for one palette colour row, from the live used rows (so a delete shows at once). */
export function availableNow(row: PaletteColourRow | undefined, rows: UsedRow[], part: string): number {
  return row ? Math.max(row.owned - usedNow(rows, part, row.colour), 0) : 0;
}

export type AddStatus = { allowed: boolean; warning: string | null; available: number | null };

/** The palette's add rule: blocked in "only" mode without a free piece, a warning in "prefer"; the server enforces it too. */
export function addStatus(mode: PartsMode, row: PaletteColourRow | undefined, rows: UsedRow[], part: string): AddStatus {
  if (mode === "off") return { allowed: true, warning: null, available: null };
  const available = availableNow(row, rows, part);
  if (available > 0) return { allowed: true, warning: null, available };
  const warning = row ? "Used up: every piece you own is already in the model" : "Not in your inventory";
  return { allowed: mode !== "only", warning, available };
}

/** The id the editor will give the next added entry: the first free eN, like ldraw_tools.editor._next_id. */
export function nextEntryId(plan: PlanLike): string {
  const used = new Set(plan.sections.flatMap(s => s.steps.flatMap(step => step.map(e => e.id))));
  let n = 1;
  while (used.has(`e${n}`)) n++;
  return `e${n}`;
}

export type SelectionPoint = { id: string; at: [number, number, number] };

/** Add at the selection's position (one brick height above it) or just above the origin; with `snap` the batch ends with a snap of the new entry. The API validates a batch against the plan before it, so a caller must send the snap as a second request (EditorPage does). */
export function addEdits(plan: PlanLike, part: string, colour: number, selection?: SelectionPoint | null, snap = true) {
  const at = selection ? [selection.at[0], selection.at[1] - 24, selection.at[2]] : [0, -24, 0];
  const add = { op: "add", section: plan.sections[0].name, part: `${partKey(part)}.dat`, colour, position: at };
  return snap ? [add, { op: "snap", id: nextEntryId(plan) }] : [add];
}
