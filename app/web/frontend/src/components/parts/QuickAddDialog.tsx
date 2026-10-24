import { useEffect, useMemo, useRef, useState } from "react";
import { collectionApi, type QuickItem } from "../../api";
import { colourHex } from "../../ldrawColours";
import Dialog from "./Dialog";
import PartThumb from "./PartThumb";
import type { OnChanged } from "./changes";

const EXAMPLE = "20 red 2x4 bricks\n3 x 3001 blue\n10 plate 1x2 dark bluish grey\n4 tan 1x1 round";

type Choice = { part?: string; colour?: number };

/** Type what you added, in your own words. Rules read it (no AI): the dialog shows what it understood, asks about anything
 *  it is not sure of, and adds nothing until you press Add. */
export default function QuickAddDialog({ onClose, onChanged }: { onClose: () => void; onChanged: OnChanged }) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<QuickItem[]>([]);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [colours, setColours] = useState<{ code: number; name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<{ pieces: number; lines: number; source: string } | null>(null);
  const seq = useRef(0);

  useEffect(() => { void collectionApi.colours().then(r => setColours(r.colours), () => undefined); }, []);
  useEffect(() => {
    if (!text.trim()) { setItems([]); setChoices({}); return; }
    const mine = ++seq.current;
    setParsing(true);
    const timer = setTimeout(() => {
      collectionApi.quickParse(text).then(r => {
        if (mine !== seq.current) return;
        setItems(r.items); setChoices({}); setError("");
      }, e => mine === seq.current && setError((e as Error).message)).finally(() => mine === seq.current && setParsing(false));
    }, 350);
    return () => clearTimeout(timer);
  }, [text]);

  const resolved = useMemo(() => items.map((it, i) => {
    const part = choices[i]?.part ?? it.part, colour = choices[i]?.colour ?? it.colour;
    return part && colour != null && it.qty ? { part, colour, qty: it.qty } : null;
  }), [items, choices]);
  const ready = resolved.filter((r): r is { part: string; colour: number; qty: number } => r !== null);
  const pieces = ready.reduce((n, r) => n + r.qty, 0);
  const pick = (i: number, c: Choice) => setChoices(prev => ({ ...prev, [i]: { ...prev[i], ...c } }));

  async function add() {
    if (!ready.length) return;
    setBusy(true); setError("");
    try {
      const r = await collectionApi.quickAdd(ready);
      setAdded({ pieces: r.pieces, lines: ready.length, source: r.source.id });
      onChanged(`Added ${r.pieces.toLocaleString()} pieces to Loose parts`, r.batch != null ? { source: r.source.id } : undefined);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (added) return <Dialog title="Quick add" onClose={onClose}>
    <p><strong>Added {added.pieces.toLocaleString()} pieces</strong> in {added.lines} line{added.lines === 1 ? "" : "s"} to Loose parts. You can undo this from the notice on the page.</p>
    <div className="form-actions">
      <button type="button" onClick={() => { setAdded(null); setText(""); }}>Add more</button>
      <button type="button" className="primary" autoFocus onClick={onClose}>Done</button>
    </div>
  </Dialog>;

  return <Dialog title="Quick add" wide busy={busy} onClose={onClose}>
    <label className="field"><span>What did you add?</span>
      <textarea autoFocus rows={4} value={text} placeholder={EXAMPLE} aria-label="Parts to add" onChange={e => setText(e.target.value)} /></label>
    <p className="muted small">One per line, or separated by commas. Part number, or size and shape words (2x4 brick, 1x1 round plate); colour names in English or Italian. Understood by rules, no AI involved.</p>
    {error && <div className="banner error" role="alert">{error}</div>}
    {parsing && !items.length && <p className="muted"><span className="spinner" aria-hidden /> Reading…</p>}
    {items.length > 0 && <ul className="quick-lines" aria-label="What was understood" aria-busy={parsing}>
      {items.map((it, i) => {
        const part = choices[i]?.part ?? it.part, colour = choices[i]?.colour ?? it.colour;
        const description = part ? (it.candidates.find(c => c.part === part)?.description ?? (part === it.part ? it.description : "")) : "";
        const ok = resolved[i] !== null;
        return <li key={`${i}-${it.line}`} className={`quick-line ${ok ? "ok" : "open"}`}>
          <div className="quick-head">
            <span className={`badge ${ok ? "ok" : "warn"}`}>{ok ? "ready" : "needs you"}</span>
            <span className="muted small ellipsis" title={it.line}>“{it.line}”</span>
          </div>
          <p className="quick-understood">
            {it.qty != null ? <strong className="mono">{it.qty.toLocaleString()} ×</strong> : null}{" "}
            {part ? <><code>{part}</code> {description}</> : <span className="muted">part not chosen</span>}{" · "}
            {colour != null ? <><span className="swatch" style={{ background: colourHex(colour) }} aria-hidden /> {colours.find(c => c.code === colour)?.name ?? it.colour_name}</> : <span className="muted">colour not chosen</span>}
            {it.qty_assumed && <span className="muted small"> (no quantity given: 1)</span>}
          </p>
          {it.notes.map((n, k) => <p key={k} className="muted small">{n}</p>)}
          {!part && it.message && <p className="warn-text small">{it.message}</p>}
          {!part && it.candidates.length > 0 && <div className="quick-candidates" role="radiogroup" aria-label="Which part?">
            {it.candidates.map(c => <button type="button" role="radio" aria-checked={choices[i]?.part === c.part} key={c.part} className="quick-candidate"
              onClick={() => pick(i, { part: c.part })}>
              <PartThumb part={c.part} size={36} /><span><code>{c.part}</code> <span className="small">{c.description}</span> <span className="muted small">{c.category}</span></span>
            </button>)}
          </div>}
          {part && colour == null && <label className="field quick-colour"><span className="small">{it.message || "Which colour?"}</span>
            <select value="" aria-label="Colour" onChange={e => e.target.value && pick(i, { colour: Number(e.target.value) })}>
              <option value="">Choose a colour…</option>
              {it.colour_candidates.length > 0 && <optgroup label="Could mean">{it.colour_candidates.map(c => <option key={c.colour} value={c.colour}>{c.name}</option>)}</optgroup>}
              <optgroup label="All colours">{colours.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}</optgroup>
            </select></label>}
        </li>;
      })}
    </ul>}
    <div className="form-actions">
      <span className="muted small quick-total" aria-live="polite">{items.length ? `${ready.length} of ${items.length} line${items.length === 1 ? "" : "s"} ready · ${pieces.toLocaleString()} pieces` : ""}</span>
      <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="button" className="primary" disabled={busy || !ready.length || parsing} onClick={() => void add()}>
        {busy ? "Adding…" : ready.length ? `Add ${pieces.toLocaleString()} pieces` : "Add"}</button>
    </div>
  </Dialog>;
}
