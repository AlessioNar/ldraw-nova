// Panel sizes of the workspace, remembered per browser. Storage may throw or be blocked: the defaults then apply.
import { useCallback, useEffect, useState } from "react";

const KEY = "obd.ws.layout";
export const PANEL = { def: 344, min: 280, maxShare: 0.6, step: 16 };
export const STRIP = { min: 56, max: 260, step: 16 };

type Saved = { panelW: number; panelCollapsed: boolean; stripH: number | null; stripThin: boolean };
const DEFAULTS: Saved = { panelW: PANEL.def, panelCollapsed: false, stripH: null, stripThin: false };

function read(): Saved {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (raw && typeof raw === "object") {
      return {
        panelW: Number.isFinite(raw.panelW) ? raw.panelW : DEFAULTS.panelW,
        panelCollapsed: raw.panelCollapsed === true,
        stripH: Number.isFinite(raw.stripH) ? raw.stripH : null,
        stripThin: raw.stripThin === true,
      };
    }
  } catch { /* storage unavailable or not JSON */ }
  return DEFAULTS;
}

export function useLayout() {
  const [saved, setSaved] = useState<Saved>(read);
  const [vw, setVw] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setVw(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  const update = useCallback((patch: Partial<Saved>) => {
    setSaved((s) => {
      const next = { ...s, ...patch };
      try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* not remembered */ }
      return next;
    });
  }, []);
  const maxW = Math.max(PANEL.min, Math.floor(vw * PANEL.maxShare));
  const clampW = (w: number) => Math.min(maxW, Math.max(PANEL.min, Math.round(w)));
  const clampH = (h: number) => Math.min(STRIP.max, Math.max(STRIP.min, Math.round(h)));
  return {
    panelW: clampW(saved.panelW), panelMin: PANEL.min, panelMax: maxW, panelCollapsed: saved.panelCollapsed,
    setPanelW: (w: number) => update({ panelW: clampW(w), panelCollapsed: false }),
    resetPanel: () => update({ panelW: PANEL.def, panelCollapsed: false }),
    togglePanel: () => update({ panelCollapsed: !saved.panelCollapsed }),
    stripH: saved.stripH === null ? null : clampH(saved.stripH), stripThin: saved.stripThin,
    setStripH: (h: number) => update({ stripH: clampH(h), stripThin: false }),
    resetStrip: () => update({ stripH: null, stripThin: false }),
    toggleStrip: () => update({ stripThin: !saved.stripThin }),
  };
}
export type Layout = ReturnType<typeof useLayout>;
