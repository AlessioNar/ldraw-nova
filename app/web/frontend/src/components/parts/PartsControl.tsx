import { Link } from "react-router-dom";
import { useState } from "react";
import { reservationsApi, type Inventory, type PartsMode } from "../../api";

export type PartsSteering = { mode: PartsMode; ids: string[] };

const KEY = "ldraw.partsSteering";
const MODES: { value: PartsMode; label: string; hint: string }[] = [
  { value: "off", label: "Off", hint: "Ignore my inventories" },
  { value: "prefer", label: "Prefer", hint: "Favour parts I own and report what is missing" },
  { value: "only", label: "Only", hint: "The model must fit my inventory" },
];

export function loadSteering(): PartsSteering {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (v && MODES.some(m => m.value === v.mode) && Array.isArray(v.ids)) return { mode: v.mode, ids: v.ids.filter((i: unknown) => typeof i === "string") };
  } catch { /* storage unavailable or corrupt */ }
  return { mode: "off", ids: [] };
}
export function saveSteering(s: PartsSteering) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage unavailable */ }
}
export const STEERING_EVENT = "obd:steering";
/** Saves the scope and tells an open composer, which keeps its own copy. */
export function applySteering(s: PartsSteering) {
  saveSteering(s);
  window.dispatchEvent(new CustomEvent(STEERING_EVENT, { detail: s }));
}

/** One-line summary for the settings popover row ("Only · My parts"). */
export function partsSummary(value: PartsSteering, inventories: Inventory[]): string {
  if (!inventories.length) return "No parts list";
  if (value.mode === "off") return "Off";
  const active = inventories.filter(i => value.ids.includes(i.id));
  return `${MODES.find(m => m.value === value.mode)?.label} · ${active.length === 1 ? active[0].name : `${active.length} lists`}`;
}

/** How the agent uses the user's inventories; rendered inline inside the chat settings popover. */
export default function PartsControl({ inventories, value, onChange, disabled, onChanged }: {
  inventories: Inventory[]; value: PartsSteering; onChange: (next: PartsSteering) => void; disabled?: boolean;
  onChanged?: () => void;   // a set was taken apart: the lists changed, reload them
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  if (!inventories.length) {
    return <Link className="parts-empty" to="/parts">Upload a parts list under My Parts</Link>;
  }
  const active = value.mode === "off" ? [] : inventories.filter(i => value.ids.includes(i.id));
  const setMode = (mode: PartsMode) => {
    const ids = active.length ? active.map(i => i.id) : [inventories[0].id];
    onChange(mode === "off" ? { mode, ids: value.ids } : { mode, ids });
  };
  const toggle = (id: string) => {
    const ids = active.map(i => i.id);
    onChange({ mode: value.mode, ids: ids.includes(id) ? ids.filter(i => i !== id) : [...ids, id] });
  };
  const takeApart = async (inv: Inventory) => {
    setBusy(inv.id); setError("");
    try { await reservationsApi.setSetState(inv.id, "parts"); onChanged?.(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  };
  const loose = inventories.filter(i => i.kind === "loose");
  return <div className="parts-control">
    <div className="segmented" role="radiogroup" aria-label="Parts mode">
      {MODES.map(m => <button type="button" key={m.value} role="radio" aria-checked={value.mode === m.value} aria-pressed={value.mode === m.value}
        title={m.hint} disabled={disabled} onClick={() => setMode(m.value)}>{m.label}</button>)}
    </div>
    <p className="muted small">{MODES.find(m => m.value === value.mode)?.hint}.</p>
    {value.mode !== "off" && <div className="parts-presets" role="group" aria-label="Which parts">
      <button type="button" disabled={disabled} onClick={() => onChange({ mode: value.mode, ids: inventories.map(i => i.id) })}>All of My Parts</button>
      {!!loose.length && <button type="button" disabled={disabled} onClick={() => onChange({ mode: value.mode, ids: loose.map(i => i.id) })}>Loose parts only</button>}
    </div>}
    {value.mode !== "off" && <fieldset className="parts-lists"><legend className="picker-label">Inventories</legend>
      {inventories.map(inv => {
        const checked = active.some(i => i.id === inv.id);
        return <label className="check" key={inv.id}>
          <input type="checkbox" checked={checked} disabled={disabled || (checked && active.length === 1)} onChange={() => toggle(inv.id)} />
          <span className="ellipsis">{inv.name}</span><span className="muted small">{inv.total_pieces.toLocaleString()} pcs</span>
          {inv.set?.state === "built" && <button type="button" className="small" disabled={disabled || busy === inv.id} title="It is built, so its parts are held back. Take it apart to use them in this build."
            onClick={e => { e.preventDefault(); void takeApart(inv); }}>Take apart</button>}
        </label>;
      })}
    </fieldset>}
    {error && <p className="warn-text small" role="alert">{error}</p>}
  </div>;
}
