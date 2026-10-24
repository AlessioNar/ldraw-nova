import { colourHex } from "../../ldrawColours";
import { useColours } from "../../workspace/colours";
import type { Workspace } from "../../workspace/useWorkspace";
import ColourPicker from "./ColourPicker";

const fmt = (v?: number[]) => (v && v.length ? v.map((n) => String(Math.round(n * 100) / 100)) : ["", "", ""]);

function Xyz({ label, values }: { label: string; values?: number[] }) {
  const v = fmt(values);
  return (
    <>
      <div className="label">{label}</div>
      <div className="ws-xyz">
        {["X", "Y", "Z"].map((axis, i) => <label key={axis}>{axis}<input className="mono" readOnly value={v[i] ?? ""} aria-label={`${label} ${axis}`} /></label>)}
      </div>
    </>
  );
}

/** What the canvas says is selected: part, colour (swatches recolour it), position and rotation, delete. */
export default function PiecePanel({ ws }: { ws: Workspace }) {
  const colours = useColours();
  const sel = ws.selection;
  const first = sel[0];
  const off = ws.mode !== "edit" || !!ws.viewing;
  if (!first) {
    return (
      <section className="ws-panel" role="tabpanel" aria-label="Piece">
        <div className="ws-empty">
          <div className="brick-row" aria-hidden="true">{[4, 14, 1, 2].map((c) => <i key={c} style={{ background: colourHex(c) }} />)}</div>
          <p><strong>Nothing selected.</strong></p>
          <p className="muted small">Click a piece in the 3D view. Shift-click adds more, drag on empty space to box-select, <kbd>G</kbd> widens to its sub-model.</p>
        </div>
      </section>
    );
  }
  const same = sel.every((p) => String(p.colour) === String(first.colour));
  const colourOf = (c: number | string) => colours.find((x) => x.code === Number(c));
  const current = same ? colourOf(first.colour) : undefined;
  return (
    <section className="ws-panel" role="tabpanel" aria-label="Piece">
      <div className="ws-piece-head">
        <span className="ws-swatch big" style={{ background: current?.hex ?? (same ? colourHex(Number(first.colour)) : "var(--surface-3)") }} />
        <div>
          {sel.length > 1
            ? <><h3>{sel.length} pieces</h3><div className="muted small">{[...new Set(sel.map((p) => p.part))].slice(0, 4).join(", ")}{new Set(sel.map((p) => p.part)).size > 4 ? "…" : ""}</div></>
            : <><h3>{first.name ?? first.part}</h3><div className="muted small"><span className="mono">{first.part}</span> · {current?.name ?? `Colour ${first.colour}`}</div></>}
        </div>
      </div>
      <div className="label">Colour{sel.length > 1 ? " for all" : ""}</div>
      <ColourPicker value={same ? Number(first.colour) : null} onPick={off ? () => {} : ws.recolourSelection} label="Piece colour" />
      {sel.length === 1 && <><Xyz label="Position (studs)" values={first.position} /><Xyz label="Rotation (degrees)" values={first.rotation} /></>}
      <div className="label">Quick actions</div>
      <div className="ws-actions">
        <button className="danger" disabled={off} onClick={() => ws.send({ cmd: "delete" })}>Delete</button>
      </div>
      <p className="muted small">Move and rotate with the tools on the left. Every change here is one step in the draft, so Undo takes it back.</p>
    </section>
  );
}
