import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useMatch } from "react-router-dom";
import { api, buildsApi, type BuildSummary, type Chat, type LlmEntry, type OlderChat } from "./api";
import { AppContext, type ViewerTarget } from "./context";
import KeyBanner from "./components/KeyBanner";
import AppBar from "./components/AppBar";
import ViewerModal from "./components/ViewerModal";
import BuildPage from "./pages/BuildPage";
import ChatPage from "./pages/ChatPage";
import EditorPage from "./pages/EditorPage";
import Home from "./pages/Home";
import NewBuild from "./pages/NewBuild";
import Builds from "./pages/Builds";
import Parts from "./pages/Parts";
import Settings from "./pages/Settings";
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

  useEffect(() => refreshBuilds(), [location.pathname, refreshBuilds]);   // versions saved elsewhere (editor, chat) show up in My Builds

  const state = useMemo(
    () => ({ chats, chatsLoaded, refreshChats, builds, olderChats, buildsLoaded, refreshBuilds, llms, defaultLlmId, defaultWorkerId, refreshLlms, openViewer, stageRef }),
    [chats, chatsLoaded, refreshChats, builds, olderChats, buildsLoaded, refreshBuilds, llms, defaultLlmId, defaultWorkerId, refreshLlms, openViewer],
  );

  const workspace = !!useMatch("/build/:id");   // the build workspace has its own top bar

  return (
    <AppContext.Provider value={state}>
      <div className="app">
        <main className="main">
          {!workspace && <AppBar />}
          <KeyBanner />
          <div className="content">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<NewBuild />} />
            <Route path="/build/:id" element={<BuildPage />} />
            <Route path="/chat/:id" element={<ChatPage />} />
            <Route path="/chat/:id/edit" element={<EditorPage />} />
            <Route path="/build/:build/edit" element={<EditorPage />} />
            <Route path="/builds" element={<Builds />} />
            <Route path="/models" element={<Navigate to="/builds" replace />} />
            <Route path="/parts" element={<Parts />} />
            <Route path="/parts/:id" element={<Parts />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </div>
        </main>
      </div>
      {viewer && <ViewerModal target={viewer} onClose={() => setViewer(null)} />}
    </AppContext.Provider>
  );
}
