import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { colourHex } from "../../ldrawColours";
import { stub } from "../../workspace/stubs";
import type { CanvasCommand, CanvasEvent, CanvasMode, CanvasPiece, Tool } from "../../workspace/types";

/** Stand-in for the W2 canvas while only the stub layer answers: it speaks the same obd: protocol (ready, selection,
 *  changed, mode) and obeys the same commands, on a handful of pretend bricks. Shown only when the stubs are active and
 *  the real editor page never says obd:ready. It writes no files and calls no API besides the stub counters. */
export type FakeHandle = { handle: (c: CanvasCommand) => void };

const SAMPLE: CanvasPiece[] = [
  { id: "e1", part: "3009", name: "Brick 1 x 6", colour: 15, position: [0, 2, 0], rotation: [0, 0, 0] },
  { id: "e2", part: "3008", name: "Brick 1 x 8", colour: 15, position: [8, 2, 3], rotation: [0, 90, 0] },
  { id: "e3", part: "60592", name: "Window 1 x 2 x 2", colour: 72, position: [8, 3, 2], rotation: [0, 0, 0] },
  { id: "e4", part: "3037", name: "Slope 45 2 x 4", colour: 1, position: [4, 9, 3], rotation: [0, 0, 0] },
  { id: "e5", part: "3003", name: "Brick 2 x 2", colour: 72, position: [6, 10, 2], rotation: [0, 0, 0] },
  { id: "e6", part: "3036", name: "Plate 6 x 8", colour: 1, position: [0, 5, -4], rotation: [0, 0, 0] },
];
const WIDTH: Record<string, number> = { "3009": 6, "3008": 8, "60592": 2, "3037": 4, "3003": 2, "3036": 6 };

const FakeCanvas = forwardRef<FakeHandle, { id: string; imageUrl?: string | null; viewing: boolean; onEvent: (e: CanvasEvent) => void }>(
  function FakeCanvas({ id, imageUrl, viewing, onEvent }, ref) {
    const [pieces, setPieces] = useState<CanvasPiece[]>(SAMPLE);
    const [selected, setSelected] = useState<string[]>([]);
    const tool = useRef<Tool>("select");
    const armed = useRef<{ part: string; colour: number } | null>(null);
    const colour = useRef(4);
    const mode = useRef<CanvasMode>("edit");
    const history = useRef<{ undo: CanvasPiece[][]; redo: CanvasPiece[][] }>({ undo: [], redo: [] });
    const live = useRef({ pieces, selected, viewing });
    live.current = { pieces, selected, viewing };

    useEffect(() => { onEvent({ type: "obd:ready", mode: mode.current }); }, [onEvent]);

    const emitSelection = useCallback((ids: string[], from: CanvasPiece[]) => {
      onEvent({ type: "obd:selection", pieces: from.filter((p) => ids.includes(p.id)) });
    }, [onEvent]);
    const edited = useCallback((next: CanvasPiece[], keep: string[]) => {
      history.current.undo.push(live.current.pieces); history.current.redo = [];
      const d = stub.edit(id);
      setPieces(next); setSelected(keep); emitSelection(keep, next);
      if (d) onEvent({ type: "obd:changed", revision: d.revision, undo_depth: d.undo_depth, redo_depth: d.redo_depth });
    }, [id, onEvent, emitSelection]);

    const handle = useCallback((c: CanvasCommand) => {
      const { pieces: ps, selected: sel } = live.current;
      if (c.cmd === "set-tool") tool.current = c.tool;
      else if (c.cmd === "set-mode") { mode.current = c.mode; onEvent({ type: "obd:mode", mode: c.mode }); }
      else if (c.cmd === "add-part") { armed.current = { part: c.part, colour: c.colour }; tool.current = "add"; }
      else if (c.cmd === "set-colour") {
        colour.current = c.colour;
        if (sel.length) edited(ps.map((p) => (sel.includes(p.id) ? { ...p, colour: c.colour } : p)), sel);
      } else if (c.cmd === "delete" && sel.length) edited(ps.filter((p) => !sel.includes(p.id)), []);
      else if (c.cmd === "undo" || c.cmd === "redo") {
        const from = c.cmd === "undo" ? history.current.undo : history.current.redo, to = c.cmd === "undo" ? history.current.redo : history.current.undo;
        const prev = from.pop();
        if (!prev) return;
        to.push(ps);
        const d = c.cmd === "undo" ? stub.undo(id) : stub.redo(id);
        setPieces(prev); setSelected([]); emitSelection([], prev);
        if (d) onEvent({ type: "obd:changed", revision: d.revision, undo_depth: d.undo_depth, redo_depth: d.redo_depth });
      }
    }, [id, onEvent, edited, emitSelection]);
    useImperativeHandle(ref, () => ({ handle }), [handle]);

    const pick = (p: CanvasPiece, e: React.MouseEvent) => {
      e.stopPropagation();
      if (viewing || mode.current !== "edit") return;
      if (tool.current === "delete") return edited(pieces.filter((x) => x.id !== p.id), []);
      if (tool.current === "paint") return edited(pieces.map((x) => (x.id === p.id ? { ...x, colour: colour.current } : x)), [p.id]);
      const ids = e.shiftKey ? (selected.includes(p.id) ? selected.filter((x) => x !== p.id) : [...selected, p.id]) : [p.id];
      setSelected(ids); emitSelection(ids, pieces);
    };
    const background = () => {
      if (viewing || mode.current !== "edit") return;
      if (tool.current === "add" && armed.current) {
        const n = pieces.length + 1, a = armed.current;
        return edited([...pieces, { id: `e${n + 10}`, part: a.part, name: `Part ${a.part}`, colour: a.colour, position: [n, 2, 0], rotation: [0, 0, 0] }], []);
      }
      if (selected.length) { setSelected([]); emitSelection([], pieces); }
    };

    return (
      <div className="ws-fake" onClick={background}>
        <span className="ws-fake-note">Stand-in canvas (stub layer): the real 3D canvas answers here once it ships</span>
        {viewing && imageUrl
          ? <img className="ws-fake-img" src={imageUrl} alt="The version being viewed" draggable={false} />
          : (
            <div className="ws-fake-scene">
              {pieces.map((p) => (
                <button key={p.id} type="button" className={`ws-fake-brick${selected.includes(p.id) ? " sel" : ""}`} onClick={(e) => pick(p, e)}
                  style={{ width: `${(WIDTH[p.part] ?? 2) * 22}px`, background: typeof p.colour === "number" ? colourHex(p.colour) : "#8a8a8a" }} title={`${p.name} · ${p.part}`}>
                  {p.part}
                </button>
              ))}
            </div>
          )}
      </div>
    );
  },
);
export default FakeCanvas;
