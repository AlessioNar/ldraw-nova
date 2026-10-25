import { useEffect, useState } from "react";
import { buildsApi, type GeneratorList } from "../../api";
import type { Workspace } from "../../workspace/useWorkspace";
import GeneratorForm from "../GeneratorForm";
import BuildingEditor from "./BuildingEditor";

const noPreview = () => {};   // regenerating is free and undoable, so the form's live preview stays off the canvas

/** Generator builds: the generator's form (the building grammar gets its component tree); Regenerate writes a new generated base into the draft (one undoable step). */
export default function SettingsPanel({ ws }: { ws: Workspace }) {
  const [list, setList] = useState<GeneratorList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const source = ws.ws?.generator;
  useEffect(() => { buildsApi.generators().then(setList).catch((e: Error) => setError(e.message)); }, []);
  const entry = list?.generators.find((g) => g.id === source?.id);
  const n = ws.changes;
  if (!source) return null;
  return (
    <section className="ws-panel" role="tabpanel" aria-label="Settings">
      <div className="ws-chat-head"><span className="label">{entry?.title ?? source.id} generator</span><span className="ws-chip mono">no AI</span></div>
      <p className="muted small ws-about">Changing a setting does nothing until you press Regenerate. It is free and takes about a second.</p>
      {n > 0 && <div className="banner warn ws-warnbox" role="status">Regenerating replaces your {n} manual {n === 1 ? "edit" : "edits"} with the new settings. Undo brings {n === 1 ? "it" : "them"} back.</div>}
      {error && <div className="banner error">{error}</div>}
      {entry && list && entry.id === "building"
        ? <BuildingEditor key={ws.draft?.base_version} generator={entry} colours={list.colours} initial={source.params}
            busy={ws.busy === "regenerate"} onSubmit={(params) => ws.regenerate(entry.id, params)} />
        : entry && list
        ? <GeneratorForm key={ws.draft?.base_version} generator={entry} colours={list.colours} initial={source.params} submitLabel="Regenerate"
            busy={ws.busy === "regenerate"} onPreview={noPreview} onSubmit={(params) => ws.regenerate(entry.id, params)} />
        : !error && <p className="muted">Loading the form…</p>}
    </section>
  );
}
