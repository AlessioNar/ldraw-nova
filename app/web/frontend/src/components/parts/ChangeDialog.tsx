import { useEffect, useState } from "react";
import { collectionApi, type PartMatch } from "../../api";
import { colourHex } from "../../ldrawColours";
import Dialog from "./Dialog";
import PartThumb from "./PartThumb";
import type { OnChanged } from "./changes";

export type Line = { part: string; colour: number; colour_name: string; qty: number; description: string };

/** Fixes imported lines: a new colour (all of a line, or just some pieces of it) and/or a different part. One undoable batch. */
export default function ChangeDialog({ source, sourceName, lines, onClose, onChanged }:
  { source: string; sourceName: string; lines: Line[]; onClose: () => void; onChanged: OnChanged }) {
  const one = lines.length === 1 ? lines[0] : null;
  const [colours, setColours] = useState<{ code: number; name: string }[]>([]);
  const [colourFilter, setColourFilter] = useState("");
  const [colour, setColour] = useState<number | null>(null);
  const [qty, setQty] = useState(String(one?.qty ?? ""));
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<PartMatch[]>([]);
  const [part, setPart] = useState<PartMatch | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { void collectionApi.colours().then(r => setColours(r.colours), e => setError((e as Error).message)); }, []);
  useEffect(() => {
    const text = query.trim();
    if (!text) { setMatches([]); return; }
    let alive = true;
    const t = setTimeout(() => collectionApi.partSearch(text, lines[0]?.part).then(r => alive && setMatches(r.parts), () => undefined), 250);
    return () => { alive = false; clearTimeout(t); };
  }, [query, lines]);

  const wanted = Math.floor(Number(qty));
  const qtyOk = !one || (wanted >= 1 && wanted <= one.qty);
  const ready = (colour != null || part != null) && qtyOk;
  const shownColours = colours.filter(c => c.name.toLowerCase().includes(colourFilter.trim().toLowerCase()));
  const colourName = colours.find(c => c.code === colour)?.name ?? "";

  async function apply() {
    setBusy(true); setError("");
    try {
      const ops = lines.map(l => ({
        op: "move" as const, part: l.part, colour: l.colour,
        ...(colour != null ? { to_colour: colour } : {}), ...(part ? { to_part: part.part } : {}),
        ...(one && wanted < one.qty ? { qty: wanted } : {}),
      }));
      const r = await collectionApi.ops(source, ops, "Fixed an import");
      const what = one ? `${one.part}` : `${lines.length} lines`;
      const to = [colourName && `colour ${colourName}`, part && `part ${part.part}`].filter(Boolean).join(" and ");
      if (r.batch != null) onChanged(`Changed ${what} in ${sourceName} to ${to}`, { source });
      onClose();
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return <Dialog title={one ? `Change ${one.part} ${one.colour_name}` : `Change ${lines.length} lines`} busy={busy} wide onClose={onClose}>
    {error && <div className="banner error" role="alert">{error}</div>}
    <p className="muted small">{one ? <>{one.description || "Unknown part"} · {one.qty.toLocaleString()} in {sourceName}</>
      : <>All pieces of the {lines.length} selected lines in {sourceName}.</>} It is one change in the source's history and can be undone.</p>
    <section className="change-section" aria-label="New colour">
      <h3>New colour</h3>
      {one && <label className="field"><span>How many pieces (the rest keep their colour, e.g. 4 of {one.qty} to split)</span>
        <input type="number" min={1} max={one.qty} value={qty} disabled={busy} onChange={e => setQty(e.target.value)} /></label>}
      <input type="search" aria-label="Filter colours" placeholder="Filter colours" value={colourFilter} onChange={e => setColourFilter(e.target.value)} />
      <div className="colour-grid" role="listbox" aria-label="Colours">{shownColours.map(c =>
        <button type="button" role="option" aria-selected={c.code === colour} className={`colour-option ${c.code === colour ? "on" : ""}`} key={c.code} disabled={busy}
          onClick={() => setColour(c.code === colour ? null : c.code)}>
          <span className="swatch" style={{ background: colourHex(c.code) }} aria-hidden /> {c.name}</button>)}</div>
    </section>
    <section className="change-section" aria-label="New part">
      <h3>Different part <span className="muted small">(keeps the colour unless you pick one above)</span></h3>
      <input type="search" aria-label="Search parts" placeholder="Search: 2x4 brick, 3001" value={query} onChange={e => setQuery(e.target.value)} />
      {part && <p className="chosen-part"><PartThumb part={part.part} size={40} /> <code>{part.part}</code> {part.description}
        <button type="button" className="link-button" onClick={() => setPart(null)}>Clear</button></p>}
      <ul className="part-matches" aria-label="Matching parts">{matches.map(m => <li key={m.part}>
        <button type="button" disabled={busy} aria-pressed={part?.part === m.part} onClick={() => setPart(m)}>
          <PartThumb part={m.part} size={40} /><code>{m.part}</code> <span>{m.description}</span></button></li>)}</ul>
    </section>
    <div className="form-actions">
      <button type="button" onClick={onClose} disabled={busy}>Cancel</button>
      <button type="button" className="primary" disabled={busy || !ready} onClick={() => void apply()}>{busy ? "Changing…" : "Change"}</button>
    </div>
  </Dialog>;
}
