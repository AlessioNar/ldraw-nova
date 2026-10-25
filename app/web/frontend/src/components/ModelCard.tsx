import { useState } from "react";
import { Link } from "react-router-dom";
import { downloadGlb, downloadUrl, timeAgo, type ChatModel, type ModelFile, type ViewerMode } from "../api";
import { useApp } from "../context";
import InfoModal from "./InfoModal";

type Props = {
  model: ModelFile & Partial<Pick<ChatModel, "warnings" | "created_at" | "version" | "latest" | "title" | "origin" | "changes">>;
  showChats?: boolean;
  onDelete?: () => Promise<void>;
  onEdit?: () => Promise<void>;      // My Builds: open the model in the 3D editor
  buildId?: string;                  // My Builds: the build this file is a version of; the card links to its page
};

function DownloadIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M8 2v8m-3-3 3 3 3-3M3 11v3h10v-3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function ModelCard({ model, showChats = false, onDelete, onEdit, buildId }: Props) {
  const { openViewer } = useApp();
  const open = (mode: ViewerMode = "viewer") =>
    model.model_url &&
    openViewer({ modelUrl: model.model_url, title: model.description || model.file, parts: model.parts, mode, version: model.version });
  const warnings = model.warnings ?? [];
  const extension = "." + (model.file.split(".").pop() ?? "mpd").toLowerCase();
  const bomBusy = model.bom_status === "queued" || model.bom_status === "rendering";
  const [glb, setGlb] = useState<{ busy: boolean; error?: string }>({ busy: false });
  const [info, setInfo] = useState(false);
  const [showWarnings, setShowWarnings] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [editing, setEditing] = useState(false);
  async function edit() {
    if (!onEdit) return;
    setEditing(true); setDeleteError("");
    try { await onEdit(); }
    catch (e) { setDeleteError((e as Error).message); setEditing(false); }
  }
  async function remove() {
    if (!onDelete || !confirm(`Delete “${model.file}” and its preview, parts list and notes from My Builds? This cannot be undone. Original chat work files are kept.`)) return;
    setDeleting(true); setDeleteError("");
    try { await onDelete(); }
    catch (e) { setDeleteError((e as Error).message); }
    finally { setDeleting(false); }
  }

  async function saveGlb() {
    if (!model.model_url) return;
    setGlb({ busy: true });
    try {
      await downloadGlb(model.model_url, model.file);
      setGlb({ busy: false });
    } catch (e) {
      setGlb({ busy: false, error: (e as Error).message });
    }
  }

  let thumb;
  if (model.image_url) {
    thumb = <img src={model.image_url} alt={model.description || model.file} loading="lazy" />;
  } else if (model.status === "queued" || model.status === "rendering") {
    thumb = (
      <span className="thumb-missing">
        <span className="spinner" aria-hidden /> {model.status === "rendering" ? "Rendering snapshot…" : "Waiting to render…"}
      </span>
    );
  } else if (model.status === "failed") {
    thumb = <span className="thumb-missing" title={model.error ?? ""}>Snapshot failed — open in 3D</span>;
  } else {
    thumb = <span className="thumb-missing">This model was deleted from My Builds</span>;
  }

  return (
    <div className={`model-card${model.latest ? " is-latest" : ""}`}>
      {onDelete && <button type="button" className="model-delete" aria-label={`Delete ${model.file}`} title="Delete model" disabled={deleting} onClick={remove}>
        {deleting ? <span className="spinner" aria-hidden="true" /> : "×"}
      </button>}
      <button className="model-thumb" onClick={() => open()} disabled={!model.model_url} title="Open in the 3D viewer">
        {thumb}
        {model.model_url && <span className="thumb-hint">View in 3D</span>}
      </button>
      <div className="model-meta">
        {deleteError && <p className="warn-text small" role="alert">{deleteError}</p>}
        <div className="model-title">
          <span className="model-file">
            {model.version != null && <span className="version-badge" title={model.latest ? "The latest version in this chat" : "An earlier version in this chat"}>v{model.version}{model.latest ? " · latest" : ""}</span>}
            {model.origin === "editor" && <span className="version-badge edited" title="You edited the plan in the 3D editor and published it">edited by you</span>}
            {buildId
              ? <Link className="ellipsis build-card-link" to={`/build/${buildId}`} title="Open the build"><strong>{model.version != null ? model.title || model.file : model.file}</strong></Link>
              : <strong className="ellipsis" title={model.file}>{model.version != null ? model.title || model.file : model.file}</strong>}
          </span>
          {warnings.length > 0 && (
            <button type="button" className="badge warn warning-toggle" aria-expanded={showWarnings} onClick={() => setShowWarnings(v => !v)}>
              {warnings.length} warning{warnings.length > 1 ? "s" : ""}
            </button>
          )}
        </div>
        <div className="model-spec" aria-label="Model facts">
          <div><span className="label">Parts</span><b>{model.parts != null ? model.parts.toLocaleString() : bomBusy ? "…" : "–"}</b></div>
          <div><span className="label">File</span><b>{model.size >= 1024 * 1024 ? `${(model.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(model.size / 1024))} KB`}</b></div>
        </div>
        {showWarnings && warnings.length > 0 && <ul className="model-warnings small">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        {model.version != null && model.version > 1 && model.changes && (model.changes.summary.length > 120
          ? <details className="model-changes small" title="Compared with the previous version's parts list">
              <summary><strong>Changed since v{model.version - 1}:</strong> {model.changes.summary.slice(0, 100).replace(/[,;]?\s+\S*$/, "")}…</summary>
              {model.changes.summary}
            </details>
          : <p className="model-changes small" title="Compared with the previous version's parts list">
              <strong>Changed since v{model.version - 1}:</strong> {model.changes.summary}
            </p>)}
        {model.info_heading && <p className="model-info-heading"><strong>{model.info_heading}</strong></p>}
        {model.description && !(model.version != null && model.title === model.description) && <p className="model-description">{model.description}</p>}
        <div className="muted small ellipsis">
          {model.version != null && <>{model.file} · </>}{timeAgo(model.created_at ?? model.mtime)}
          {showChats && model.chats && model.chats.length > 0 && (
            <>
              {" · from "}
              {model.chats.map((c, i) => (
                <span key={c.id}>
                  {i > 0 && ", "}
                  <Link to={`/chat/${c.id}`}>{c.title}</Link>
                </span>
              ))}
            </>
          )}
        </div>
        <div className="model-actions">
          {model.info_url && (
            <button className="model-info-action" onClick={() => setInfo(true)} title="About this model, from its author (e.g. the prompt that made it)">
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
                <circle cx="8" cy="8" r="6" /><path d="M8 7v4M8 4.5v1" strokeLinecap="round" />
              </svg>
              Info
            </button>
          )}
          {buildId && <Link className="button primary-link" to={`/build/${buildId}`} title="Open this model's build: versions, settings, editor and chats">Open build</Link>}
          <span className="model-viewer-actions" role="group" aria-label="View model">
            <button onClick={() => open()} disabled={!model.model_url}>
              3D View
            </button>
            {onEdit && (
              <button onClick={edit} disabled={!model.model_url || editing} title="Move, add or remove bricks by hand, then save the result as a new version">
                {editing ? <span className="spinner" aria-hidden /> : null} Edit in 3D
              </button>
            )}
            <button onClick={() => open("player")} disabled={!model.model_url} title="Watch the model being built, step by step">
              3D Player
            </button>
          </span>
          <span className="button-group model-download-actions" role="group" aria-label="Download">
            {model.model_url && (
              <a className="button" href={downloadUrl(model.model_url)} title={`Download ${model.file}`}>
                <DownloadIcon /> {extension}
              </a>
            )}
            {model.model_url && (
              <button
                onClick={saveGlb}
                disabled={glb.busy}
                title={
                  glb.busy
                    ? "Preparing .glb… conversion can take a minute if needed"
                    : glb.error
                      ? `Download failed: ${glb.error}`
                      : "Download .glb (uses the existing file when available)"
                }
                aria-label={glb.busy ? "Preparing .glb download" : undefined}
                className={glb.error ? "danger-text" : undefined}
              >
                {glb.busy ? <span className="spinner" aria-hidden /> : <DownloadIcon />}
                .glb
              </button>
            )}
            {model.bom_url ? (
              <a className="button" href={downloadUrl(model.bom_url)} title="Download the bill of materials (CSV)">
                <DownloadIcon /> BOM
              </a>
            ) : model.model_url ? (
              <button disabled title={bomBusy ? "Generating the bill of materials…" : (model.bom_error ?? "No bill of materials")}>
                <DownloadIcon /> BOM
              </button>
            ) : null}
          </span>
        </div>
      </div>
      {info && model.info_url && (
        <InfoModal title={model.description || model.file} url={model.info_url} onClose={() => setInfo(false)} />
      )}
    </div>
  );
}
