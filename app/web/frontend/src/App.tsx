import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, buildsApi, type BuildSummary, type Chat, type LlmEntry, type OlderChat } from "./api";
import { AppContext, type ViewerTarget } from "./context";
import KeyBanner from "./components/KeyBanner";
import Sidebar from "./components/Sidebar";
import ViewerModal from "./components/ViewerModal";
import BuildPage from "./pages/BuildPage";
import ChatPage from "./pages/ChatPage";
import EditorPage from "./pages/EditorPage";
import Home from "./pages/Home";
import NewBuild from "./pages/NewBuild";
import Models from "./pages/Models";
import Parts from "./pages/Parts";
import Settings from "./pages/Settings";
import { usePersistentFlag } from "./hooks/usePersistentFlag";
import { setProviderCatalogue } from "./modelChoices";

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatsLoaded, setChatsLoaded] = useState(false);
  const [builds, setBuilds] = useState<BuildSummary[]>([]);
  const [olderChats, setOlderChats] = useState<OlderChat[]>([]);
  const [buildsLoaded, setBuildsLoaded] = useState(false);
  const [llms, setLlms] = useState<LlmEntry[]>([]);
  const [defaultLlmId, setDefaultLlmId] = useState<string | null>(null);
  const [defaultWorkerId, setDefaultWorkerId] = useState<string | null>(null);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = usePersistentFlag("ldraw-nova.sidebar", "collapsed", "expanded", false);
  const [, setProvidersLoaded] = useState(false);
  const location = useLocation();
  const stageRef = useRef<((target: ViewerTarget) => void) | null>(null);
  const openViewer = useCallback((target: ViewerTarget) => (stageRef.current ?? setViewer)(target), []);

  const refreshChats = useCallback(() => {
    api
      .chats()
      .then((r) => {
        setChats(r.chats);
        setChatsLoaded(true);
      })
      .catch(() => {});
  }, []);
  const refreshBuilds = useCallback(() => {
    buildsApi
      .list()
      .then((r) => {
        setBuilds(r.builds);
        setOlderChats(r.older_chats);
        setBuildsLoaded(true);
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
    refreshBuilds();
    refreshLlms();
    // Labels live in a module map; the state flip re-renders the tree once they land.
    api
      .providers()
      .then((r) => {
        setProviderCatalogue(r.catalogue);
        setProvidersLoaded(true);
      })
      .catch(() => {});
  }, [refreshChats, refreshBuilds, refreshLlms]);

  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => refreshBuilds(), [location.pathname, refreshBuilds]);   // versions saved elsewhere (editor, chat) show up in the sidebar

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
    () => ({ chats, chatsLoaded, refreshChats, builds, olderChats, buildsLoaded, refreshBuilds, llms, defaultLlmId, defaultWorkerId, refreshLlms, openViewer, stageRef }),
    [chats, chatsLoaded, refreshChats, builds, olderChats, buildsLoaded, refreshBuilds, llms, defaultLlmId, defaultWorkerId, refreshLlms, openViewer],
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
          <KeyBanner />
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<NewBuild />} />
            <Route path="/build/:id" element={<BuildPage />} />
            <Route path="/chat/:id" element={<ChatPage />} />
            <Route path="/chat/:id/edit" element={<EditorPage />} />
            <Route path="/build/:build/edit" element={<EditorPage />} />
            <Route path="/models" element={<Models />} />
            <Route path="/parts" element={<Parts />} />
            <Route path="/parts/:id" element={<Parts />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
      {viewer && <ViewerModal target={viewer} onClose={() => setViewer(null)} />}
    </AppContext.Provider>
  );
}
