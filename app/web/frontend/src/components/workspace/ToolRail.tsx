import type { CanvasMode, Tool } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
import Icon from "./Icon";

const TOOLS: { tool: Tool; key: string; title: string; danger?: boolean }[] = [
  { tool: "select", key: "V", title: "Select (V): click a piece, Shift-click to add" },
  { tool: "move", key: "M", title: "Move (M): drag an arrow, snaps to studs, Alt for free" },
  { tool: "rotate", key: "R", title: "Rotate (R): 15 degree steps, Alt for free" },
  { tool: "add", key: "B", title: "Add brick (B): pick a part in the Parts tab, click to place" },
  { tool: "paint", key: "P", title: "Paint (P): click a piece to give it the chosen colour" },
  { tool: "delete", key: "Del", title: "Delete (Del): click a piece to remove it", danger: true },
];
const MODES: { mode: CanvasMode; icon: string; label: string; title: string }[] = [
  { mode: "edit", icon: "pencil", label: "Edit", title: "Edit: tools on" },
  { mode: "view", icon: "eye", label: "View", title: "View: orbit and inspect, nothing can change" },
  { mode: "player", icon: "play", label: "Player", title: "Player: step through the build, brick by brick" },
];

/** Tools (left rail on a desktop, bottom toolbar on a phone) and the Edit / View / Player switch. */
export default function ToolRail({ ws }: { ws: Workspace }) {
  const off = ws.mode !== "edit" || !!ws.viewing;
  return (
    <nav className="ws-rail" aria-label="Tools">
      {!ws.phone && (
        <div className="ws-tools">
          {TOOLS.map((t) => (
            <button key={t.tool} className={`ws-tool${t.danger ? " danger-tool" : ""}`} aria-pressed={ws.tool === t.tool} title={t.title} disabled={off}
              onClick={() => (t.tool === "delete" && ws.selection.length ? ws.send({ cmd: "delete" }) : ws.setTool(t.tool))}>
              <Icon name={t.tool} size={22} /><kbd>{t.key}</kbd>
            </button>
          ))}
        </div>
      )}
      <div className="ws-modes" role="group" aria-label="Canvas mode">
        {MODES.filter((m) => !ws.phone || m.mode !== "edit").map((m) => (
          <button key={m.mode} className="ws-mode" aria-pressed={ws.mode === m.mode} title={m.title} onClick={() => ws.setMode(m.mode)}>
            <Icon name={m.icon} size={20} /><span>{m.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}
