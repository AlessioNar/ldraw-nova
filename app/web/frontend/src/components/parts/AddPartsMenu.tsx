import { useEffect, useRef, useState } from "react";

export type AddWay = "quick" | "set" | "import";
const WAYS: { id: AddWay; title: string; hint: string }[] = [
  { id: "quick", title: "Quick add", hint: "type “20 red 2x4 bricks”" },
  { id: "set", title: "Add a set", hint: "by number or name (8448, tipper truck)" },
  { id: "import", title: "Import a file", hint: "Brickit / Pileometer, Rebrickable CSV" },
];

/** The one entry point for every way parts come in. */
export default function AddPartsMenu({ onPick }: { onPick: (way: AddWay) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    addEventListener("mousedown", away); addEventListener("keydown", esc);
    return () => { removeEventListener("mousedown", away); removeEventListener("keydown", esc); };
  }, [open]);
  return <div className="add-menu" ref={box}>
    <button type="button" className="primary" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>+ Add parts ▾</button>
    {open && <ul className="add-menu-list" role="menu">{WAYS.map(w => <li key={w.id} role="none">
      <button type="button" role="menuitem" onClick={() => { setOpen(false); onPick(w.id); }}><strong>{w.title}</strong><span className="muted small">{w.hint}</span></button></li>)}</ul>}
  </div>;
}
