import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, reservationsApi, type ModelFile, type Reservation } from "../api";
import ModelCard from "../components/ModelCard";
import BuiltButton from "../components/parts/BuiltButton";

// Opening the page scans data/generated; the backend queues snapshots for models
// that have none, and we poll until they're all rendered.
const POLL_MS = 1500;

/** The per-file gallery (downloads, per-file delete, zip of everything): mounted only when its disclosure on My Builds is open. */
export default function ModelFiles() {
  const [models, setModels] = useState<ModelFile[] | null>(null);
  const [pending, setPending] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [reserved, setReserved] = useState<Record<string, Reservation>>({});
  const deleted = useRef(new Set<string>());
  const navigate = useNavigate();

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const r = await api.models();
        if (!alive) return;
        setModels(r.models.filter(model => !deleted.current.has(model.file)));
        setPending(r.pending);
        setError(null);
        if (r.pending > 0) timer = setTimeout(load, POLL_MS);
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  const loadReserved = () => reservationsApi.list().then(r => setReserved(Object.fromEntries(r.reservations.map(x => [x.model, x]))), () => undefined);
  useEffect(() => { void loadReserved(); }, []);

  const q = query.trim().toLowerCase();
  const shown = (models ?? []).filter(
    (m) =>
      !q ||
      m.file.toLowerCase().includes(q) ||
      (m.info_heading ?? "").toLowerCase().includes(q) ||
      m.description.toLowerCase().includes(q) ||
      (m.chats ?? []).some((c) => c.title.toLowerCase().includes(q)),
  );

  return (
    <div className="model-files">
      <header className="page-head">
        <div>
          <p className="muted">
            Every model file on disk, including ones no build owns. Open a model to explore it in 3D.
            {pending > 0 && (
              <>
                {" "}
                <span className="warn-text">
                  <span className="spinner" aria-hidden /> Processing {pending} model{pending > 1 ? "s" : ""}…
                </span>
              </>
            )}
          </p>
        </div>
        <div className="head-actions">
          <input type="search" aria-label="Filter models" placeholder="Filter by name, description or chat" value={query} onChange={(e) => setQuery(e.target.value)} />
          {models && models.length > 0 && (
            <a className="button" href="/api/models/zip">
              Download all (.zip)
            </a>
          )}
        </div>
      </header>
      {error && <div className="banner error">{error}</div>}
      {models === null && !error && <p className="muted">Loading models…</p>}
      {models?.length === 0 && (
        <p className="muted">No models yet. Ask for one in a chat, or drop .mpd/.ldr/.dat files into data/generated.</p>
      )}
      {models && models.length > 0 && shown.length === 0 && <p className="muted">No models match your search.</p>}
      <div className="card-grid">
        {shown.map((m) => (
          <ModelCard key={m.file} model={m} showChats buildId={m.build}
            extraActions={<BuiltButton file={m.file} title={m.description || m.file} reservation={reserved[m.file]} onChanged={() => void loadReserved()} />} onEdit={async () => {
            const opened = await api.editModel(m.file);
            navigate(`/build/${encodeURIComponent(opened.build)}/edit`);
          }} onDelete={async () => {
            await api.deleteModel(m.file);
            deleted.current.add(m.file);
            void loadReserved();
            setModels(current => current?.filter(model => model.file !== m.file) ?? null);
          }} />
        ))}
      </div>
    </div>
  );
}
