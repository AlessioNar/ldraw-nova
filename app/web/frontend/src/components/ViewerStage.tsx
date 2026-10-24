import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { viewerUrl, type ViewerMode } from "../api";
import type { ViewerTarget } from "../context";

const MODES: { mode: ViewerMode; label: string; title: string }[] = [
  { mode: "viewer", label: "3D View", title: "Inspect the model (three.js viewer)" },
  { mode: "player", label: "3D Player", title: "Watch it being built, step by step" },
];

/** The 3D stage beside the conversation (wide chat page): one embedded viewer page, switched between versions and modes.
 *  Loaded lazily by ChatPage, so the chat itself stays light. */
export default function ViewerStage({ editorPath, target, emptyText, onOpenEditor }: { editorPath: string; target: ViewerTarget | null; emptyText?: string; onOpenEditor?: () => void }) {
  const [mode, setMode] = useState<ViewerMode>(target?.mode ?? "viewer");
  useEffect(() => setMode(target?.mode ?? "viewer"), [target]);

  const editor = onOpenEditor
    ? <button className="stage-link" onClick={onOpenEditor} title="Place and move parts in 3D">Open editor</button>
    : <Link className="button stage-link" to={editorPath} title="Place and move parts in 3D">Open editor</Link>;
  if (!target) {
    return (
      <section className="chat-stage" aria-label="3D stage">
        <header className="stage-head">
          <span className="label">3D stage</span>
          {editor}
        </header>
        <div className="stage-empty">
          <div className="brick-row" aria-hidden="true"><i /><i /><i /><i /></div>
          <strong>Your model will appear here</strong>
          <p className="muted">{emptyText ?? "Once the agent has built something, it opens in 3D on this stage. Older versions open here too."}</p>
        </div>
      </section>
    );
  }
  const url = viewerUrl(target.modelUrl, target.parts, mode);
  const label = `${mode === "player" ? "3D player" : "3D view"} of ${target.title}`;
  return (
    <section className="chat-stage" aria-label="3D stage">
      <header className="stage-head">
        {target.version != null && <span className="version-badge">v{target.version}</span>}
        <strong className="ellipsis" title={target.title}>{target.title}</strong>
        <span className="segmented" role="group" aria-label="Show in">
          {MODES.map((m) => (
            <button key={m.mode} aria-pressed={mode === m.mode} title={m.title} onClick={() => setMode(m.mode)}>{m.label}</button>
          ))}
        </span>
        {editor}
        <a className="stage-link" href={url} target="_blank" rel="noreferrer">Open in new tab</a>
      </header>
      {/* key: a fresh page per model and mode, so the iframe's history doesn't pile up */}
      <iframe key={url} className="stage-frame" src={url} title={label} allow="fullscreen" />
    </section>
  );
}
