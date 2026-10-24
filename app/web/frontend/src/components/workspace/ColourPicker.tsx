import { COMMON_COLOURS, useColours } from "../../workspace/colours";

/** A row of common LDraw colours and a menu for the rest. */
export default function ColourPicker({ value, onPick, label }: { value: number | null; onPick: (code: number) => void; label: string }) {
  const colours = useColours();
  const common = COMMON_COLOURS.map((c) => colours.find((x) => x.code === c)).filter((c) => !!c);
  const known = colours.find((c) => c.code === value);
  return (
    <div className="ws-colours" role="group" aria-label={label}>
      <div className="ws-swatches">
        {common.map((c) => (
          <button key={c.code} type="button" className="ws-sw" aria-pressed={c.code === value} title={`${c.name} (${c.code})`} aria-label={c.name}
            style={{ background: c.hex }} onClick={() => onPick(c.code)} />
        ))}
        {known && !common.includes(known) && (
          <button type="button" className="ws-sw" aria-pressed title={`${known.name} (${known.code})`} aria-label={known.name} style={{ background: known.hex }} />
        )}
      </div>
      {colours.length > 0 && (
        <select aria-label="More colours" value="" onChange={(e) => e.target.value && onPick(Number(e.target.value))}>
          <option value="">More colours…</option>
          {colours.map((c) => <option key={c.code} value={c.code}>{c.name} ({c.code})</option>)}
        </select>
      )}
    </div>
  );
}
