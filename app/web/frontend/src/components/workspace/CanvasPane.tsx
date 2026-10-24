import { useEffect, useMemo, useRef, useState } from "react";
import { colourHex } from "../../ldrawColours";
import { useColours } from "../../workspace/colours";
import type { CanvasCommand, CanvasEvent } from "../../workspace/types";
import type { Workspace } from "../../workspace/useWorkspace";
import { usingStubs } from "../../workspace/wsApi";
import FakeCanvas, { type FakeHandle } from "./FakeCanvas";
import Icon from "./Icon";

const READY_WAIT_MS = 3500;

function useTheme(): "dark" | "light" {
  const read = (): "dark" | "light" => {
    const forced = document.documentElement.dataset.theme;
    return forced === "light" || forced === "dark" ? forced : matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  };
  const [theme, setTheme] = useState(read);
  useEffect(() => {
    const update = () => setTheme(read());
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const mq = matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", update);
    return () => { mo.disconnect(); mq.removeEventListener("change", update); };
  }, []);
  return theme;
}

/** The canvas iframe (viewer/editor.html in embed mode) plus the workspace's overlays on top of it. */
export default function CanvasPane({ ws }: { ws: Workspace }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const fakeRef = useRef<FakeHandle>(null);
  const theme = useTheme();
  const initialMode = useRef(ws.phone ? "view" : "edit");
  const [fake, setFake] = useState(false);
  const colours = useColours();
  const { id, registerCanvas, onCanvasEvent } = ws;
  const loaded = !!ws.draft;
  const src = useMemo(() => `/viewer/editor.html?space=builds&id=${encodeURIComponent(id)}&embed=1&mode=${initialMode.current}&theme=${theme}`, [id, theme]);

  // messages in: only from our own frame (or the stand-in)
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow || e.origin !== location.origin) return;
      const d = e.data as CanvasEvent | undefined;
      if (typeof d?.type === "string" && d.type.startsWith("obd:")) { setFake(false); onCanvasEvent(d); }
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [onCanvasEvent]);

  // commands out
  useEffect(() => {
    registerCanvas((c: CanvasCommand) => {
      if (fakeRef.current) fakeRef.current.handle(c);
      else frame.current?.contentWindow?.postMessage({ type: "obd:command", ...c }, location.origin);
    });
  }, [registerCanvas, fake]);

  // no obd:ready while the stubs are active: the real canvas is not there yet, show the stand-in
  useEffect(() => {
    if (!loaded || ws.canvasReady || fake) return;
    const t = setTimeout(() => { if (usingStubs()) setFake(true); }, READY_WAIT_MS);
    return () => clearTimeout(t);
  }, [loaded, ws.canvasReady, fake, src]);

  const viewing = ws.viewing;
  const viewedRow = viewing?.kind === "version" ? ws.versions.find((v) => v.version === viewing.version) : undefined;
  const lead = ws.selection[0];
  const colourName = (c: number | string) => colours.find((x) => x.code === Number(c))?.name ?? String(c);
  const parts = ws.check?.needed ?? ws.versions.find((v) => v.version === ws.baseVersion)?.parts;
  const shownParts = viewedRow?.parts ?? parts;

  return (
    <main className="ws-stage">
      {loaded && <iframe key={src} ref={frame} className="ws-frame" src={src} title="3D canvas" allow="fullscreen" />}
      {fake && <FakeCanvas ref={fakeRef} id={id} viewing={!!viewing} imageUrl={viewedRow?.detail?.image_url ?? ws.versions.at(-1)?.detail?.image_url} onEvent={onCanvasEvent} />}
      {!loaded && <div className="ws-loading muted"><span className="spinner" aria-hidden /> Opening the build…</div>}

      {viewing && (
        <div className="ws-ro" role="status">
          {viewing.kind === "version" ? (
            <span>Viewing <strong>v{viewing.version}</strong> (read-only){viewedRow && <span className="muted"> · {viewedRow.parts.toLocaleString()} parts</span>}</span>
          ) : <span>Previewing the AI proposal <span className="muted">· read-only, not in your draft yet</span></span>}
          <span className="ws-spacer" />
          {viewing.kind === "version" && <button className="primary" disabled={ws.busy === "branch"} onClick={() => ws.branch(viewing.version)}>Branch from here</button>}
          {viewing.kind === "proposal" && (() => {
            const p = ws.proposals.find((x) => x.id === viewing.id);
            return p && <button className="primary" disabled={ws.busy === "proposal"} onClick={() => ws.keepProposal(p)}>Keep in draft</button>;
          })()}
          <button onClick={ws.backToDraft}>{ws.changes > 0 || viewing.kind === "proposal" ? "Back to draft" : `Back to v${ws.baseVersion}`}</button>
        </div>
      )}

      {ws.canvasReady || fake ? (
        <>
          <div className={`ws-overlay ws-tl${viewing ? " lower" : ""}`}>
            <button className="ws-icon-btn solid" title="Reset view (Home)" aria-label="Reset view" onClick={() => ws.send({ cmd: "focus" })}><Icon name="fit" /></button>
          </div>
          {shownParts != null && <div className={`ws-overlay ws-tr${viewing ? " lower" : ""}`}><span className="ws-chip mono">{shownParts.toLocaleString()} parts</span></div>}
        </>
      ) : null}

      {lead && !viewing && (
        <div className="ws-selchip" role="status">
          {ws.selection.length > 1
            ? <strong>{ws.selection.length} pieces selected</strong>
            : <><strong>{lead.name ?? lead.part}</strong> <span className="mono">{lead.part} · {colourName(lead.colour)}</span></>}
        </div>
      )}
      {ws.empty && loaded && (
        <div className="ws-empty-canvas">
          <div className="ws-empty-card">
            <div className="brick-row" aria-hidden="true">{[4, 14, 1, 2].map((c) => <i key={c} style={{ background: colourHex(c) }} />)}</div>
            <p><strong>Nothing here yet.</strong></p>
            <p className="muted">Describe what you want below, or pick a part and click to place it.</p>
            <div className="ws-empty-actions">
              <button className="primary" onClick={() => document.getElementById("ws-describe-input")?.focus()}>Describe it</button>
              <button onClick={() => ws.setTab("parts")}>Pick a part</button>
              {!ws.phone && <button onClick={() => ws.setTab("ai")}>Ask the AI</button>}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
