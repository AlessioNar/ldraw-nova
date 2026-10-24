import { useEffect, useState } from "react";
import { paletteApi, type EditorColour } from "../api";

let cache: Promise<EditorColour[]> | null = null;
/** The LDraw colour list (name, code, hex), fetched once per session. */
export function useColours(): EditorColour[] {
  const [colours, setColours] = useState<EditorColour[]>([]);
  useEffect(() => {
    cache ??= paletteApi.colours().then((r) => r.colours).catch(() => { cache = null; return []; });
    let live = true;
    cache.then((c) => { if (live) setColours(c); });
    return () => { live = false; };
  }, []);
  return colours;
}

/** The handful shown first in every colour picker; the rest sit in the "More colours" menu. */
export const COMMON_COLOURS = [4, 1, 14, 2, 15, 0, 72, 71, 19, 25, 6, 320, 272, 85];
