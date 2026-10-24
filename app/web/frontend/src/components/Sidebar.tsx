import { Link, NavLink, useMatch, useNavigate } from "react-router-dom";
import { api, timeAgo } from "../api";
import { useApp } from "../context";

const RECENT = 12;

export default function Sidebar({ collapsed, onToggle, onClose }: { collapsed: boolean; onToggle: () => void; onClose: () => void }) {
  const { builds, olderChats, buildsLoaded, refreshChats, refreshBuilds } = useApp();
  const navigate = useNavigate();
  const currentChat = useMatch("/chat/:id")?.params.id;
  const onBuild = useMatch("/build/:id");
  const onBuildEditor = useMatch("/build/:id/edit");
  const currentBuild = (onBuild ?? onBuildEditor)?.params.id;

  async function remove(chat: { id: string; title: string }) {
    if (!confirm(`Delete "${chat.title}"?\n\nThis removes data/chats/${chat.id}/ and the agent's work folder data/output/${chat.id}/. Its models stay in data/generated.`)) return;
    await api.deleteChat(chat.id);
    refreshChats();
    refreshBuilds();
    if (currentChat === chat.id) navigate("/");
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <Link to="/" className="brand-home" title="Home"><svg className="brand-mark" viewBox="3 7 26 19" fill="none" aria-hidden><path fill="currentColor" d="M15.2 11.5H12.5v-2.3a.7.7 0 0 0-.7-.7H6.2a.7.7 0 0 0-.7.7v2.3H4.5A1.5 1.5 0 0 0 3 13v10.5A1.5 1.5 0 0 0 4.5 25h10.7z"/><path stroke="currentColor" strokeWidth="1.6" strokeDasharray="2.6 1.9" strokeLinecap="round" d="M17.6 12.3h2.7V9.3h5.4v3h1.8a.9.9 0 0 1 .9.9v10.1a.9.9 0 0 1-.9.9h-9.9"/></svg>
          <span className="brand-name label">Open Brick Designer</span></Link>
        <button
          className="icon-button sidebar-toggle"
          onClick={onToggle}
          title={collapsed ? "Expand sidebar (Ctrl+B)" : "Collapse sidebar (Ctrl+B)"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? "»" : "«"}
        </button>
        <button className="icon-button sidebar-close" onClick={onClose} title="Close menu" aria-label="Close menu">
          ✕
        </button>
      </div>
      <NavLink className="button new-chat" to="/new" title="New build" aria-label="New build">
        <span className="nav-icon" aria-hidden>+</span>
        <span className="label">New build</span>
      </NavLink>
      <nav className="nav">
        <NavLink to="/models" title="My Builds" aria-label="My Builds">
          <span className="nav-icon" aria-hidden>▣</span>
          <span className="label">My Builds</span>
        </NavLink>
        <NavLink to="/parts" title="My Parts" aria-label="My Parts">
          <span className="nav-icon" aria-hidden>▤</span>
          <span className="label">My Parts</span>
        </NavLink>
      </nav>

      <div className="section-label label">Builds</div>
      <div className="chat-list">
        {buildsLoaded && builds.length === 0 && <p className="muted small pad">No builds yet. Start one with New build.</p>}
        {builds.slice(0, RECENT).map((b) => (
          <div key={b.id} className={`chat-item build-item ${currentBuild === b.id ? "active" : ""}`}>
            <NavLink to={`/build/${b.id}`} className="chat-link" title={b.title}>
              {b.latest?.image_url
                ? <img className="build-thumb" src={b.latest.image_url} alt="" loading="lazy" />
                : <span className="build-thumb empty" aria-hidden />}
              <span className="build-text">
                <span className="chat-title">{b.title}</span>
                <span className="build-sub">{b.versions > 0 ? `v${b.versions}` : "no version yet"} · {timeAgo(b.updated_at)}</span>
              </span>
            </NavLink>
          </div>
        ))}
        {builds.length > RECENT && <NavLink className="more-builds small" to="/models">All {builds.length} builds →</NavLink>}
        {olderChats.length > 0 && (
          <details className="older-chats">
            <summary className="label">Older chats ({olderChats.length})</summary>
            {olderChats.map((chat) => (
              <div key={chat.id} className={`chat-item ${currentChat === chat.id ? "active" : ""}`}>
                <NavLink to={`/chat/${chat.id}`} className="chat-link" title={chat.title}>
                  <span className="chat-title">
                    {chat.running && <span className="live-dot" title="Working…" />}
                    {chat.title}
                  </span>
                  <span className="chat-time">{timeAgo(chat.updated_at)}</span>
                </NavLink>
                <button className="chat-delete" title="Delete chat" aria-label={`Delete ${chat.title}`} onClick={() => remove(chat)}>
                  ×
                </button>
              </div>
            ))}
          </details>
        )}
      </div>
      <nav className="sidebar-footer">
        <NavLink to="/settings" title="Settings" aria-label="Settings">
          <span className="nav-icon" aria-hidden>⚙</span>
          <span className="label">Settings</span>
        </NavLink>
      </nav>
    </aside>
  );
}
