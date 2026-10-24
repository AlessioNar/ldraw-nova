import { useCallback, useEffect, useState } from "react";
import { collectionApi, type ReviewGroup, type ReviewItem } from "../../api";
import { colourHex } from "../../ldrawColours";
import PartThumb from "./PartThumb";
import type { OnChanged } from "./changes";

/** One waiting piece: pick its exact colour (all of it, or part of the quantity), or say it is not yours. */
function Item({ group, item, colours, busy, run }: {
  group: ReviewGroup; item: ReviewItem; colours: { code: number; name: string }[]; busy: boolean;
  run: (job: () => Promise<{ text: string; source: string } | null>) => void;
}) {
  const [qty, setQty] = useState(String(item.qty));
  useEffect(() => setQty(String(item.qty)), [item.qty]);
  const amount = Math.min(item.qty, Math.max(1, Math.floor(Number(qty) || item.qty)));
  const resolve = (colour: number, name: string) => run(async () => {
    const r = await collectionApi.resolveReview({ source: group.source, id: item.id, colour, qty: amount });
    return r.batch != null ? { text: `${amount} × ${item.part} is now ${name}`, source: group.source } : null;
  });
  return <li className="review-item">
    <PartThumb part={item.part} size={44} />
    <div className="review-info">
      <p><code>{item.part}</code> {item.description || <span className="muted">Unknown part</span>} <strong className="mono">× {item.qty}</strong></p>
      <p className="muted small">{item.note ? `${item.note}` : ""}{item.brickit_part && item.brickit_part !== item.part ? ` · scanned as ${item.brickit_part}` : ""}</p>
      <div className="review-choices" role="group" aria-label={`Colour for ${item.part}`}>
        {item.candidates.map(c => <button type="button" key={c.colour} className="chip choice" disabled={busy} title={c.owned ? "You already own this part in this colour" : undefined}
          onClick={() => resolve(c.colour, c.name)}><span className="swatch" style={{ background: colourHex(c.colour) }} aria-hidden />{c.name}{c.owned && <span className="muted small"> ✓</span>}</button>)}
        <select aria-label={`Other colour for ${item.part}`} value="" disabled={busy}
          onChange={e => { const c = colours.find(x => x.code === Number(e.target.value)); if (c) resolve(c.code, c.name); }}>
          <option value="">Other colour…</option>{colours.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</select>
        {item.qty > 1 && <label className="review-qty small">for <input type="number" min={1} max={item.qty} aria-label={`How many of ${item.part}`} value={qty} disabled={busy} onChange={e => setQty(e.target.value)} /> of {item.qty}</label>}
        <button type="button" className="danger-text" disabled={busy} onClick={() => run(async () => {
          const r = await collectionApi.resolveReview({ source: group.source, id: item.id, discard: true });
          return r.batch != null ? { text: `Discarded ${item.qty} × ${item.part} (not mine)`, source: group.source } : null;
        })}>Not mine</button>
      </div>
    </div>
  </li>;
}

/** Colour-family pieces (Brickit says “Reds”, “Grays”) wait here. They are never counted as owned until you give them a colour. */
export default function ReviewQueue({ version, onChanged }: { version: number; onChanged: OnChanged }) {
  const [data, setData] = useState<{ groups: ReviewGroup[]; items: number; pieces: number } | null>(null);
  const [colours, setColours] = useState<{ code: number; name: string }[]>([]);
  const [familyPick, setFamilyPick] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => collectionApi.review().then(r => { setData(r); setError(""); }, e => setError((e as Error).message)), []);
  useEffect(() => { void load(); }, [load, version]);
  useEffect(() => { void collectionApi.colours().then(r => setColours(r.colours), () => undefined); }, []);

  const run = (job: () => Promise<{ text: string; source: string } | null>) => {
    setBusy(true); setError("");
    job().then(r => { if (r) onChanged(r.text, { source: r.source }); return load(); }, e => setError((e as Error).message)).finally(() => setBusy(false));
  };
  const key = (g: ReviewGroup) => `${g.source}/${g.family}`;

  return <section className="review-queue" aria-label="Review">
    <p className="muted">These pieces were scanned with a colour family only (“Reds”, “Grays”), so their exact colour is unknown. They are <strong>not counted</strong> as owned, in what you can build, or by the editor until you choose a colour for each, or for a whole family at once.</p>
    {error && <div className="banner error" role="alert">{error}</div>}
    {!data && !error && <p className="muted"><span className="spinner" aria-hidden /> Loading…</p>}
    {data && !data.groups.length && <div className="panel parts-empty-state"><h2>Nothing to review</h2><p className="muted">Pieces from a Brickit scan that only name a colour family will wait here.</p></div>}
    {data && data.groups.length > 0 && <>
      <p className="review-summary" aria-live="polite"><strong>{data.pieces.toLocaleString()}</strong> pieces in {data.items} line{data.items === 1 ? "" : "s"} waiting for a colour</p>
      {data.groups.map(g => <article className="panel review-group" key={key(g)}>
        <header>
          <h2>{g.family || "Unknown family"} <span className="badge">{g.pieces.toLocaleString()} pieces</span></h2>
          <span className="muted small">{g.source_name}</span>
          <div className="review-family-action">
            <label className="small">Give all {g.family} the colour
              <select value={familyPick[key(g)] ?? ""} disabled={busy} onChange={e => setFamilyPick({ ...familyPick, [key(g)]: e.target.value })}>
                <option value="">Choose…</option>
                {g.candidates.map(c => <option key={c.colour} value={c.colour}>{c.name}</option>)}
                <optgroup label="Any colour">{colours.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</optgroup>
              </select></label>
            <button type="button" disabled={busy || !familyPick[key(g)]} onClick={() => run(async () => {
              const code = Number(familyPick[key(g)]);
              const r = await collectionApi.resolveReview({ source: g.source, family: g.family, colour: code });
              return { text: `All ${g.family} (${g.pieces} pieces) are now ${colours.find(c => c.code === code)?.name ?? "assigned"}`, source: r.source.id };
            })}>Apply to all</button>
          </div>
        </header>
        <ul>{g.items.map(it => <Item key={it.id} group={g} item={it} colours={colours} busy={busy} run={run} />)}</ul>
      </article>)}
    </>}
  </section>;
}
