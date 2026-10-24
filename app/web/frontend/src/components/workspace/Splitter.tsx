import { useRef, type KeyboardEvent, type PointerEvent } from "react";

/** A drag handle between two areas. `edge` says where the area it resizes lies: "panel" (right of it, grows to the left)
 *  or "strip" (below it, grows upwards). Pointer drag, arrow keys when focused (Home/End jump to min/max), double-click
 *  resets, and a button collapses the area. */
export default function Splitter({ edge, value, min, max, step, label, collapsed, onChange, onReset, onToggle, hidden }: {
  edge: "panel" | "strip"; value: number; min: number; max: number; step: number; label: string; collapsed: boolean;
  onChange: (v: number) => void; onReset: () => void; onToggle: () => void; hidden?: boolean;
}) {
  const drag = useRef<{ at: number; start: number } | null>(null);
  const horizontal = edge === "panel";
  const pos = (e: { clientX: number; clientY: number }) => (horizontal ? e.clientX : e.clientY);
  const grow = horizontal ? "ArrowLeft" : "ArrowUp";
  const shrink = horizontal ? "ArrowRight" : "ArrowDown";

  const down = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return;
    e.currentTarget.setPointerCapture(e.pointerId);        // keeps the drag alive over the 3D canvas frame
    drag.current = { at: pos(e), start: value };
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (drag.current) onChange(drag.current.start + (drag.current.at - pos(e)));
  };
  const up = () => { drag.current = null; };
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === grow) onChange(value + step);
    else if (e.key === shrink) onChange(value - step);
    else if (e.key === "Home") onChange(min);
    else if (e.key === "End") onChange(max);
    else if (e.key === "Enter") onToggle();
    else return;
    e.preventDefault();
  };
  return (
    <div className={`ws-split ${horizontal ? "v" : "h"}${collapsed ? " collapsed" : ""}`} data-edge={edge} role="separator" aria-orientation={horizontal ? "vertical" : "horizontal"}
      aria-label={label} aria-valuenow={value} aria-valuemin={min} aria-valuemax={max} aria-hidden={hidden || undefined} tabIndex={hidden ? -1 : 0}
      title={`Drag to resize, double-click to reset. With the keyboard: ${grow.replace("Arrow", "")} grows, ${shrink.replace("Arrow", "")} shrinks.`}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onDoubleClick={onReset} onKeyDown={key}>
      <span className="ws-grip" aria-hidden />
      <button type="button" className="ws-split-btn" aria-expanded={!collapsed} aria-label={`${collapsed ? "Expand" : "Collapse"} ${label.toLowerCase()}`}
        onClick={onToggle} onDoubleClick={(e) => e.stopPropagation()}>
        {horizontal ? (collapsed ? "‹" : "›") : (collapsed ? "˄" : "˅")}
      </button>
    </div>
  );
}
