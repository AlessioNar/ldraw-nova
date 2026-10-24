import { createContext, useContext, type MutableRefObject } from "react";
import type { BuildSummary, Chat, LlmEntry, OlderChat, ViewerMode } from "./api";

export type ViewerTarget = { modelUrl: string; title: string; parts?: number | null; mode?: ViewerMode; version?: number };

export type AppState = {
  chats: Chat[];
  chatsLoaded: boolean;
  refreshChats: () => void;
  /** Recent builds for the sidebar, and chats that belong to no build ("Older chats"). */
  builds: BuildSummary[];
  olderChats: OlderChat[];
  buildsLoaded: boolean;
  refreshBuilds: () => void;
  llms: LlmEntry[];
  defaultLlmId: string | null;
  defaultWorkerId: string | null;
  refreshLlms: () => void;
  openViewer: (target: ViewerTarget) => void;
  /** Set by the chat page while its 3D stage is on screen (wide screens): openViewer then shows the model there instead of in the modal. */
  stageRef: MutableRefObject<((target: ViewerTarget) => void) | null>;
};

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside <AppContext.Provider>");
  return ctx;
}
