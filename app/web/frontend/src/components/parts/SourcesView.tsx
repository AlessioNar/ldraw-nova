import { useState } from "react";
import { Link } from "react-router-dom";
import { inventoryApi, timeAgo, type Inventory } from "../../api";
import SourceHistory from "./SourceHistory";
import type { OnChanged } from "./changes";

const KIND = { import: "File import", set: "Set", loose: "Loose parts" } as const;

function describe(s: Inventory): string {
  if (s.kind === "set" && s.set) return `Set ${s.set.set_num}${s.set.copies > 1 ? ` × ${s.set.copies}` : ""} · ${s.set.state === "built" ? "built" : "taken apart"}`;
  if (s.kind === "loose") return "Manual and quick adds";
  return `${s.source_filename ?? "No source file"} · ${timeAgo(s.created_at)}`;
}

/** Every list that adds up to the collection: what it holds, where it came from, its history, replace by a newer file. */
export default function SourcesView({ sources, version, onShow, onReplace, onChanged, onRemoved }:
  { sources: Inventory[]; version: number; onShow: (id: string) => void; onReplace: (id: string) => void; onChanged: OnChanged; onRemoved: () => void }) {
  const [history, setHistory] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function remove(s: Inventory) {
    if (!confirm(`Delete "${s.name}" and its history?\n\nIts parts leave the collection. Chats that use it will no longer steer towards these parts.`)) return;
    setBusy(true); setError("");
    try { await inventoryApi.remove(s.id); onRemoved(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section aria-label="Sources">
    <p className="muted">The collection is all of these added together. Each keeps its own history, so a wrong import or a typo can be undone, and a newer file replaces a list without breaking chats that use it.</p>
    {error && <div className="banner error" role="alert">{error}</div>}
    <div className="card-grid parts-grid">{sources.map(s => <article className="panel inventory-card source-card" key={s.id}>
      <p className="source-kind"><span className="badge">{KIND[s.kind ?? "import"]}</span></p>
      <h2 className="ellipsis" title={s.name}>{s.virtual ? s.name : <Link to={`/parts/${s.id}`}>{s.name}</Link>}</h2>
      <p className="muted small ellipsis">{describe(s)}</p>
      <p className="inventory-pieces"><strong>{s.total_pieces.toLocaleString()}</strong> pieces</p>
      <p className="muted small">{s.distinct_parts.toLocaleString()} distinct parts · {s.distinct_colours.toLocaleString()} colours</p>
      {(s.review_pieces ?? 0) > 0 && <p className="small"><span className="badge warn">{s.review_pieces!.toLocaleString()} maybe</span></p>}
      {(s.unmapped_parts > 0 || s.unmapped_colours > 0) && <p className="small"><span className="badge warn">{s.unmapped_parts + s.unmapped_colours} unrecognised</span></p>}
      <div className="card-actions source-actions">
        <button type="button" onClick={() => onShow(s.id)} disabled={s.total_pieces === 0 && !s.review_pieces}>Show parts</button>
        {((s.kind ?? "import") === "import" || (s.kind === "set" && s.set?.manual)) && !s.virtual && <button type="button" onClick={() => onReplace(s.id)}>{s.kind === "set" ? "Import parts" : "Replace file"}</button>}
        {!s.virtual && <button type="button" aria-expanded={history === s.id} onClick={() => setHistory(history === s.id ? "" : s.id)}>History</button>}
        {!s.virtual && <button type="button" className="danger-text" disabled={busy} aria-label={`Delete ${s.name}`} onClick={() => void remove(s)}>Delete</button>}
      </div>
      {history === s.id && <SourceHistory source={s.id} version={version} onChanged={onChanged} />}
    </article>)}</div>
  </section>;
}
