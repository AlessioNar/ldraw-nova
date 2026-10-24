import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ORIGIN_LABEL, timeAgo } from "../api";
import { confirmDeleteBuild } from "../buildActions";
import { useApp } from "../context";
import ModelFiles from "./Models";

/** My Builds: every build as a big card, newest first; chats without a build and the raw model files tuck away below. */
export default function Builds() {
  const { builds, olderChats, buildsLoaded, refreshBuilds, refreshChats } = useApp();
  const [query, setQuery] = useState("");
  const [filesOpen, setFilesOpen] = useState(false);

  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () => [...builds].sort((a, b) => b.updated_at - a.updated_at).filter((b) => !q || b.title.toLowerCase().includes(q)),
    [builds, q],
  );

  async function removeChat(chat: { id: string; title: string }) {
    if (!confirm(`Delete "${chat.title}"?\n\nThis removes data/chats/${chat.id}/ and the agent's work folder data/output/${chat.id}/. Its models stay in data/generated.`)) return;
    await api.deleteChat(chat.id);
    refreshChats();
    refreshBuilds();
  }

  return (
    <div className="page builds-page">
      <header className="page-head">
        <div>
          <h1>My Builds</h1>
          <p className="muted">Every build, newest first. Open one to work on it in 3D.</p>
        </div>
        <div className="head-actions">
          <input type="search" aria-label="Filter builds" placeholder="Filter by name" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </header>
      {!buildsLoaded
        ? <div className="wb-cards">{[0, 1, 2, 3].map((i) => <div key={i} className="wb-card skeleton" aria-hidden />)}</div>
        : (
          <div className="wb-cards">
            {!q && (
              <Link to="/new" className="wb-card wb-new" title="Start a new build">
                <span className="wb-new-plus" aria-hidden>+</span>
                <strong>New build</strong>
                <span className="muted small">Describe it, pick a type, import or start empty</span>
              </Link>
            )}
            {shown.map((b) => {
              const origin = b.latest_origin;
              return (
                <div key={b.id} className="wb-card-wrap">
                  <Link to={`/build/${b.id}`} className="wb-card" title={b.title}>
                    <span className="wb-thumb">{b.latest?.image_url ? <img src={b.latest.image_url} alt="" loading="lazy" /> : <i aria-hidden>no preview yet</i>}</span>
                    <span className="wb-meta">
                      <strong className="ellipsis">{b.title}</strong>
                      <span className="muted small">{b.versions > 0 ? `v${b.versions}` : "no version yet"} · {timeAgo(b.updated_at)}</span>
                      {origin && ORIGIN_LABEL[origin] && <span className={`origin-badge origin-${origin}`}>{ORIGIN_LABEL[origin]}</span>}
                    </span>
                  </Link>
                  <button type="button" className="icon-button wb-delete" title="Delete build" aria-label={`Delete build ${b.title}`}
                    onClick={async () => { if (await confirmDeleteBuild(b)) refreshBuilds(); }}>✕</button>
                </div>
              );
            })}
          </div>
        )}
      {buildsLoaded && q && shown.length === 0 && <p className="muted">No builds match your search.</p>}

      {olderChats.length > 0 && (
        <details className="shelf older-chats">
          <summary><span className="label">Older chats ({olderChats.length})</span><span className="muted small"> chats that belong to no build</span></summary>
          <ul className="older-list">
            {olderChats.map((chat) => (
              <li key={chat.id}>
                <Link to={`/chat/${chat.id}`} title={chat.title}>
                  {chat.running && <span className="live-dot" title="Working…" />}
                  <span className="ellipsis">{chat.title}</span>
                  <span className="muted small mono">{timeAgo(chat.updated_at)}</span>
                </Link>
                <button className="icon-button" title="Delete chat" aria-label={`Delete ${chat.title}`} onClick={() => removeChat(chat)}>✕</button>
              </li>
            ))}
          </ul>
        </details>
      )}

      <details className="shelf" onToggle={(e) => setFilesOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary><span className="label">All model files</span><span className="muted small"> downloads and per-file delete</span></summary>
        {filesOpen && <ModelFiles />}
      </details>
    </div>
  );
}
