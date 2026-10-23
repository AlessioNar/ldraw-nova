import { NavLink, useMatch, useNavigate } from "react-router-dom";
import { api, timeAgo, type Chat } from "../api";
import { useApp } from "../context";

export default function Sidebar({ collapsed, onToggle, onClose }: { collapsed: boolean; onToggle: () => void; onClose: () => void }) {
  const { chats, chatsLoaded, refreshChats, openViewer } = useApp();
  const navigate = useNavigate();
  const current = useMatch("/chat/:id")?.params.id;

  async function remove(chat: Chat) {
    if (!confirm(`Delete "${chat.title}"?\n\nThis removes data/chats/${chat.id}/ and the agent's work folder data/output/${chat.id}/. Its models stay in data/generated.`)) return;
    await api.deleteChat(chat.id);
    refreshChats();
    if (current === chat.id) navigate("/");
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden />
        <span className="brand-name label">LDraw Nova</span>
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
      <NavLink className="button new-chat" to="/new" title="New chat" aria-label="New chat">
        <span className="nav-icon" aria-hidden>+</span>
        <span className="label">New chat</span>
      </NavLink>
      <nav className="nav">
        <NavLink to="/models" title="My Models" aria-label="My Models">
          <span className="nav-icon" aria-hidden>▣</span>
          <span className="label">My Models</span>
        </NavLink>
        <NavLink to="/parts" title="My Parts" aria-label="My Parts">
          <span className="nav-icon" aria-hidden>▤</span>
          <span className="label">My Parts</span>
        </NavLink>
      </nav>

      <div className="section-label label">History</div>
      <div className="chat-list">
        {chatsLoaded && chats.length === 0 && <p className="muted small pad">No chats yet.</p>}
        {chats.map((chat) => {
          const shots = (chat.models ?? []).filter((m) => m.image_url && m.model_url).slice(-4).reverse();
          return (
            <div key={chat.id} className={`chat-item ${current === chat.id ? "active" : ""}`}>
              <NavLink to={`/chat/${chat.id}`} className="chat-link" title={chat.title}>
                <span className="chat-title">
                  {chat.running && <span className="live-dot" title="Working…" />}
                  {chat.title}
                </span>
                <span className="chat-time">{timeAgo(chat.updated_at)}</span>
              </NavLink>
              {shots.length > 0 && (
                <div className="chat-thumbs">
                  {shots.map((m) => (
                    <button
                      key={m.id}
                      className="chat-thumb"
                      title={`${m.name} — open in 3D`}
                      onClick={() => openViewer({ modelUrl: m.model_url!, title: m.description || m.name, parts: m.parts })}
                    >
                      <img src={m.image_url!} alt={m.name} loading="lazy" />
                    </button>
                  ))}
                </div>
              )}
              <button className="chat-delete" title="Delete chat" aria-label={`Delete ${chat.title}`} onClick={() => remove(chat)}>
                ×
              </button>
            </div>
          );
        })}
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
