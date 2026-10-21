import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Inventory, PartsMode } from "../../api";

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

/** Composer control: how the agent uses the user's inventories this turn. */
export default function PartsControl({ inventories, value, onChange, disabled }: {
  inventories: Inventory[]; value: PartsSteering; onChange: (next: PartsSteering) => void; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (!inventories.length) {
    return <Link className="compact-control parts-empty" to="/parts" title="Upload a parts list under My Parts">Upload a parts list under My Parts</Link>;
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
  const summary = value.mode === "off" ? "Off" : `${MODES.find(m => m.value === value.mode)?.label} · ${active.length === 1 ? active[0].name : `${active.length} lists`}`;

  return <span className="parts-control" ref={root}>
    <button type="button" className={`compact-control parts-trigger ${value.mode !== "off" ? "on" : ""}`} aria-haspopup="dialog" aria-expanded={open}
      title="Steer the agent towards the parts you own" disabled={disabled} onClick={() => setOpen(!open)}>
      <span className="parts-trigger-label">Parts: {summary}</span><span className="chevron" aria-hidden>▾</span>
    </button>
    {open && <div className="parts-popover" role="dialog" aria-label="Parts steering">
      <div className="segmented" role="radiogroup" aria-label="Parts mode">
        {MODES.map(m => <button type="button" key={m.value} role="radio" aria-checked={value.mode === m.value} aria-pressed={value.mode === m.value}
          title={m.hint} onClick={() => setMode(m.value)}>{m.label}</button>)}
      </div>
      <p className="muted small">{MODES.find(m => m.value === value.mode)?.hint}.</p>
      {value.mode !== "off" && <fieldset className="parts-lists"><legend className="picker-label">Inventories</legend>
        {inventories.map(inv => {
          const checked = active.some(i => i.id === inv.id);
          return <label className="check" key={inv.id}>
            <input type="checkbox" checked={checked} disabled={checked && active.length === 1} onChange={() => toggle(inv.id)} />
            <span className="ellipsis">{inv.name}</span><span className="muted small">{inv.total_pieces.toLocaleString()} pcs</span>
          </label>;
        })}
      </fieldset>}
    </div>}
  </span>;
}
