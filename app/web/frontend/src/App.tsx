import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, type Chat, type LlmEntry } from "./api";
import { AppContext, type ViewerTarget } from "./context";
import Sidebar from "./components/Sidebar";
import ViewerModal from "./components/ViewerModal";
import ChatPage from "./pages/ChatPage";
import EditorPage from "./pages/EditorPage";
import Home from "./pages/Home";
import Models from "./pages/Models";
import Parts from "./pages/Parts";
import Settings from "./pages/Settings";
import { usePersistentFlag } from "./hooks/usePersistentFlag";
import { setProviderCatalogue } from "./modelChoices";

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatsLoaded, setChatsLoaded] = useState(false);
  const [llms, setLlms] = useState<LlmEntry[]>([]);
  const [defaultLlmId, setDefaultLlmId] = useState<string | null>(null);
  const [defaultWorkerId, setDefaultWorkerId] = useState<string | null>(null);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = usePersistentFlag("ldraw-nova.sidebar", "collapsed", "expanded", false);
  const [, setProvidersLoaded] = useState(false);
  const location = useLocation();

  const refreshChats = useCallback(() => {
    api
      .chats()
      .then((r) => {
        setChats(r.chats);
        setChatsLoaded(true);
      })
      .catch(() => {});
  }, []);
  const refreshLlms = useCallback(() => {
    api
      .llmModels()
      .then((r) => {
        setLlms(r.models);
        setDefaultLlmId(r.default_id);
        setDefaultWorkerId(r.worker_id ?? null);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshChats();
    refreshLlms();
    // Labels live in a module map; the state flip re-renders the tree once they land.
    api
      .providers()
      .then((r) => {
        setProviderCatalogue(r.catalogue);
        setProvidersLoaded(true);
      })
      .catch(() => {});
  }, [refreshChats, refreshLlms]);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
      else if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setCollapsed(!collapsed);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [collapsed, setCollapsed]);

  const state = useMemo(
    () => ({ chats, chatsLoaded, refreshChats, llms, defaultLlmId, defaultWorkerId, refreshLlms, openViewer: setViewer }),
    [chats, chatsLoaded, refreshChats, llms, defaultLlmId, defaultWorkerId, refreshLlms],
  );

  return (
    <AppContext.Provider value={state}>
      <div className={`app ${menuOpen ? "menu-open" : ""} ${collapsed ? "sidebar-collapsed" : ""}`}>
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} onClose={() => setMenuOpen(false)} />
        <div className="scrim" onClick={() => setMenuOpen(false)} />
        <main className="main">
          <button className="menu-button" aria-label="Open menu" title="Menu" onClick={() => setMenuOpen(true)}>
            ☰
          </button>
          <Routes>
            <Route path="/" element={<Navigate to="/new" replace />} />
            <Route path="/new" element={<Home />} />
            <Route path="/chat/:id" element={<ChatPage />} />
            <Route path="/chat/:id/edit" element={<EditorPage />} />
            <Route path="/models" element={<Models />} />
            <Route path="/parts" element={<Parts />} />
            <Route path="/parts/:id" element={<Parts />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/new" replace />} />
          </Routes>
        </main>
      </div>
      {viewer && <ViewerModal target={viewer} onClose={() => setViewer(null)} />}
    </AppContext.Provider>
  );
}
