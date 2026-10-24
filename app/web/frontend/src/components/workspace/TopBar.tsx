import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { buildsApi, downloadGlb, downloadUrl, type GeneratorEntry } from "../../api";
import type { Workspace } from "../../workspace/useWorkspace";
import Icon from "./Icon";

const KIND_TAG: Record<string, string> = { ai: "AI build", import: "Imported" };

/** "← Builds", the build name (click to rename), generator tag, draft status, undo/redo, discard and save. */
export default function TopBar({ ws }: { ws: Workspace }) {
  const { detail, draft, changes, viewing, nextVersion, baseVersion, latest, empty } = ws;
  const [renaming, setRenaming] = useState<string | null>(null);
  const [generators, setGenerators] = useState<GeneratorEntry[]>([]);
  const generatorId = ws.ws?.generator?.id;
  useEffect(() => { if (generatorId) buildsApi.generators().then((g) => setGenerators(g.generators)).catch(() => {}); }, [generatorId]);
  if (!detail) return <header className="ws-top" />;

  const build = detail.build;
  const tag = generatorId ? `Generator: ${generators.find((g) => g.id === generatorId)?.title ?? generatorId}` : KIND_TAG[detail.source.kind];
  const done = async () => {
    const title = renaming?.trim();
    setRenaming(null);
    if (title && title !== build.title) ws.rename(title);
  };

  let status: { cls: string; text: React.ReactNode };
  if (viewing?.kind === "version") status = { cls: "ro", text: <>Viewing v{viewing.version}<span className="ws-w muted"> · read-only</span></> };
  else if (viewing) status = { cls: "ro", text: <>Previewing an AI proposal<span className="ws-w muted"> · read-only</span></> };
  else if (empty) status = { cls: "draft", text: <>New build<span className="ws-w"> · empty draft</span></> };
  else if (changes > 0) {
    status = { cls: "draft", text: <>v{baseVersion} · draft, {changes}<span className="ws-w"> {changes === 1 ? "change" : "changes"}</span><span className="ws-auto"> · autosaved</span></> };
  } else status = { cls: "", text: <>{latest ? `v${baseVersion} · saved` : "Empty build"}</> };

  const shown = ws.versions.find((v) => v.version === (viewing?.kind === "version" ? viewing.version : baseVersion))?.detail;
  const canSave = !viewing && changes > 0;

  return (
    <header className="ws-top">
      <Link className="ws-back" to="/" title="Back to all builds">← Builds</Link>
      <span className="ws-sep" />
      <Icon name="logo" size={20} />
      {renaming == null
        ? <h1 className="ws-name ellipsis" title="Click to rename" tabIndex={0} onClick={() => setRenaming(build.title)}
            onKeyDown={(e) => { if (e.key === "Enter") setRenaming(build.title); }}>{build.title}</h1>
        : <input className="ws-rename" autoFocus value={renaming} maxLength={120} aria-label="Build name" onChange={(e) => setRenaming(e.target.value)} onBlur={done}
            onKeyDown={(e) => { if (e.key === "Enter") done(); if (e.key === "Escape") setRenaming(null); }} />}
      {tag && <span className="ws-chip kind" title={generatorId ? "This build was made by a generator" : "How this build started"}>{tag}</span>}
      <span className="ws-spacer" />
      <span className={`ws-status ${status.cls}`} role="status">{status.text}</span>
      {shown?.model_url && (
        <details className="ws-menu">
          <summary className="ws-icon-btn" title={`Download v${shown.version}`} aria-label="Download this version"><Icon name="download" /></summary>
          <div className="ws-menu-pop">
            <span className="label">Download v{shown.version}</span>
            <a className="button" href={downloadUrl(shown.model_url)}>.{shown.file.split(".").pop()}</a>
            <button onClick={() => downloadGlb(shown.model_url!, shown.file).catch((e: Error) => ws.say(e.message, { error: true }))}>.glb</button>
            {shown.bom_url && <a className="button" href={downloadUrl(shown.bom_url)}>BOM</a>}
          </div>
        </details>
      )}
      <span className="ws-group">
        <button className="ws-icon-btn" title="Undo (Ctrl+Z)" disabled={!!viewing || !draft || draft.undo_depth === 0} onClick={ws.undo}><Icon name="undo" /><span className="ws-lbl">Undo</span></button>
        <button className="ws-icon-btn" title="Redo (Ctrl+Shift+Z)" disabled={!!viewing || !draft || draft.redo_depth === 0} onClick={ws.redo}><Icon name="redo" /><span className="ws-lbl">Redo</span></button>
      </span>
      <button className="ws-discard" disabled={!!viewing || changes === 0} onClick={ws.discard} title="Throw the draft away and go back to the last saved version">Discard draft</button>
      <button className="primary ws-save" disabled={!canSave || ws.busy === "save"} onClick={ws.save} title="Turn the draft into a version (Ctrl+S)">
        {ws.busy === "save" && <span className="spinner" aria-hidden />} Save as v{nextVersion}
      </button>
    </header>
  );
}
