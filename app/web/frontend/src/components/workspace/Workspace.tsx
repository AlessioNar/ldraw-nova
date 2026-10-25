import { useEffect, type CSSProperties } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useLayout } from "../../workspace/useLayout";
import { useWorkspace, type Tab } from "../../workspace/useWorkspace";
import ChatPanel from "./ChatPanel";
import BottomBar from "./BottomBar";
import CanvasPane from "./CanvasPane";
import PartsPanel from "./PartsPanel";
import PiecePanel from "./PiecePanel";
import SettingsPanel from "./SettingsPanel";
import TemplatePanel from "./TemplatePanel";
import Splitter from "./Splitter";
import ToolRail from "./ToolRail";
import TopBar from "./TopBar";

/** /build/:id: the always-editable build workspace (layout per docs/plans/build-workspace/mockup.html). */
export default function Workspace({ id }: { id: string }) {
  const ws = useWorkspace(id);
  const hasGenerator = !!ws.ws?.generator;
  const layout = useLayout();
  const [params] = useSearchParams();
  const wantChat = params.get("tab") === "chat";
  const setTab = ws.setTab;
  useEffect(() => { if (wantChat) setTab("chat"); }, [wantChat, setTab, id]);
  const tabs: { tab: Tab; label: string; dot?: boolean }[] = [
    { tab: "chat", label: "Chat", dot: ws.proposals.length > 0 },
    ...(ws.phone ? [] : [{ tab: "parts" as Tab, label: "Parts" }, { tab: "piece" as Tab, label: "Piece", dot: ws.selection.length > 0 }]),
    ...(hasGenerator ? [{ tab: "settings" as Tab, label: "Settings" }] : []),
    ...(ws.tab === "template" ? [{ tab: "template" as Tab, label: "Template" }] : []),      // opened from the build menu, gone when left
  ];
  const tab = tabs.some((t) => t.tab === ws.tab) ? ws.tab : tabs[0].tab;
  useEffect(() => { if (tab !== ws.tab) ws.setTab(tab, false); }, [tab, ws]);

  if (ws.notFound) return <div className="page"><p className="muted">This build doesn't exist (any more). <Link to="/new">Start a new one</Link>.</p></div>;
  if (!ws.detail || !ws.ws) return <div className="page"><p className="muted"><span className="spinner" aria-hidden /> Loading…</p></div>;

  return (
    <div className={`ws${ws.viewing ? " ro" : ""}${ws.sheetOpen ? " sheet-open" : ""}${layout.panelCollapsed ? " panel-collapsed" : ""}`} data-mode={ws.mode} data-tool={ws.tool}
      style={{ "--ws-panel-w": `${layout.panelCollapsed ? 0 : layout.panelW}px` } as CSSProperties}>
      <TopBar ws={ws} />
      <ToolRail ws={ws} />
      <CanvasPane ws={ws} />
      <Splitter edge="panel" label="Side panel width" value={layout.panelW} min={layout.panelMin} max={layout.panelMax} step={16} collapsed={layout.panelCollapsed}
        onChange={layout.setPanelW} onReset={layout.resetPanel} onToggle={layout.togglePanel} hidden={ws.phone} />
      <aside className="ws-panels" aria-label="Panels">
        <button className="ws-handle" aria-label="Expand or collapse the panel" aria-expanded={ws.sheetOpen} onClick={() => ws.setSheetOpen(!ws.sheetOpen)}><span /></button>
        <div className="ws-tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.tab} role="tab" aria-selected={tab === t.tab} onClick={() => ws.setTab(t.tab)}>{t.label}{t.dot && <i className="ws-dot" aria-label="has something for you" />}</button>
          ))}
        </div>
        {tab === "parts" && <PartsPanel ws={ws} />}
        {tab === "piece" && <PiecePanel ws={ws} />}
        {tab === "chat" && <ChatPanel ws={ws} />}
        {tab === "settings" && <SettingsPanel ws={ws} />}
        {tab === "template" && <TemplatePanel ws={ws} />}
      </aside>
      <BottomBar ws={ws} layout={layout} />

      {ws.toast && (
        <div className={`ws-toast${ws.toast.error ? " error" : ""}`} role="status">
          <span>{ws.toast.text}</span>
          {ws.toast.action && <button onClick={() => { ws.toast!.action!.run(); ws.setToast(null); }}>{ws.toast.action.label}</button>}
          <button className="icon-button" aria-label="Dismiss" onClick={() => ws.setToast(null)}>×</button>
        </div>
      )}
      {ws.confirm && (
        <div className="ws-confirm-back" onClick={() => ws.setConfirm(null)}>
          <div className="ws-confirm" role="alertdialog" aria-label={ws.confirm.title} onClick={(e) => e.stopPropagation()}>
            <h3>{ws.confirm.title}</h3>
            <p className="muted">{ws.confirm.body}</p>
            <div className="gen-actions">
              <button autoFocus onClick={() => ws.setConfirm(null)}>Cancel</button>
              <button className={ws.confirm.danger ? "danger" : "primary"} onClick={() => { const c = ws.confirm!; ws.setConfirm(null); c.onConfirm(); }}>{ws.confirm.confirmLabel}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
