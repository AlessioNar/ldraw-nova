import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { editorApi, editorUrl } from "../api";
import type { PartsMode } from "../api";
import Palette from "../components/editor/Palette";
import type { PlanLike, SelectionPoint, UsedRow } from "../components/editor/paletteLogic";

type Bridge = {
  revision: number;
  plan: PlanLike;
  parts: { mode: PartsMode; rows: UsedRow[] };
  selection: { id: string; position: [number, number, number]; section: string; leaf: boolean; repeated: boolean }[];
};

// The 3D editor is its own page (viewer/editor.html) in an iframe, for the same
// reason the viewer is: its three.js/ldbi scripts are classic globals. It reports its
// plan, used rows and selection by postMessage; the palette beside it adds parts.
export default function EditorPage() {
  const { id = "" } = useParams();
  const frame = useRef<HTMLIFrameElement>(null);
  const [bridge, setBridge] = useState<Bridge | null>(null);
  const revision = useRef(0);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow || e.origin !== location.origin) return;
      const d = e.data;
      if (d?.source !== "ldraw-editor" || d.type !== "state") return;
      revision.current = d.revision;
      setBridge(d as Bridge);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const reload = () => frame.current?.contentWindow?.postMessage({ type: "reload" }, location.origin);

  const onAdd = useCallback(async (edits: Record<string, unknown>[]) => {
    try {
      // One batch (add + snap), so one undo removes the part. If the snap is refused (nothing to snap to) the
      // part is added where it was dropped; an add that is refused itself (inventory) fails the same way again.
      try { await editorApi.edits(id, edits, revision.current); }
      catch (e) {
        const adds = edits.filter(x => x.op !== "snap");
        if ((e as { status?: number }).status !== 400 || adds.length === edits.length) throw e;
        await editorApi.edits(id, adds, revision.current);
      }
    } catch (e) {
      if ((e as { status?: number }).status === 409) reload();      // stale: fetch the current plan before retrying
      throw e;
    }
    reload();
  }, [id]);

  const first = bridge?.selection[0];
  const selection: SelectionPoint | null = first
    ? { id: first.id, at: first.position, section: first.section, leaf: first.leaf, repeated: first.repeated } : null;

  return (
    <div className="editor-page">
      <header className="chat-head">
        <Link to={`/chat/${id}`}>← Back to the chat</Link>
      </header>
      <div className="editor-body">
        <iframe ref={frame} className="editor-frame" src={editorUrl(id)} title="3D model editor"
          onLoad={() => frame.current?.contentWindow?.postMessage({ type: "ping" }, location.origin)} />
        {bridge && (
          <div className="editor-side">
            <Palette chatId={id} plan={bridge.plan} mode={bridge.parts.mode} usedRows={bridge.parts.rows}
              selection={selection} onAdd={onAdd} />
          </div>
        )}
      </div>
    </div>
  );
}
