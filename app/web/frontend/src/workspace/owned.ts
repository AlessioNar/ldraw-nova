import { useEffect, useState } from "react";
import { inventoryApi, request } from "../api";
import { partKey } from "../components/editor/paletteLogic";

export type OwnedLine = { part: string; description: string; colour: number; colourName: string; owned: number };
const PAGE = 200;

let cache: Promise<OwnedLine[]> | null = null;
/** Everything the person owns, all parts lists together (the parts check counts the whole collection). Fetched once a session. */
function loadOwned(): Promise<OwnedLine[]> {
  cache ??= (async () => {
    const merged = new Map<string, OwnedLine>();
    for (const inv of (await inventoryApi.list()).inventories) {
      for (let offset = 0; ; offset += PAGE) {
        const d = await inventoryApi.detail(inv.id, { limit: PAGE, offset });
        for (const r of d.rows) {
          const key = `${partKey(r.part)}|${r.colour}`;
          const have = merged.get(key);
          if (have) have.owned += r.qty;
          else merged.set(key, { part: partKey(r.part), description: r.description, colour: r.colour, colourName: r.colour_name, owned: r.qty });
        }
        if (offset + PAGE >= d.total || d.rows.length === 0) break;
      }
    }
    return [...merged.values()].sort((a, b) => a.part.localeCompare(b.part, undefined, { numeric: true }));
  })().catch(() => { cache = null; return []; });
  return cache;
}

/** Owned lines plus what the draft uses of each (from the editor session's parts rows, refetched after every edit). */
export function useOwned(buildId: string, tick: number) {
  const [lines, setLines] = useState<OwnedLine[] | null>(null);
  const [used, setUsed] = useState<Map<string, number>>(new Map());
  useEffect(() => { let live = true; loadOwned().then((l) => { if (live) setLines(l); }); return () => { live = false; }; }, []);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      request<{ parts?: { rows?: { part: string; colour: number; used: number }[] } }>(`/api/builds/${encodeURIComponent(buildId)}/editor`)
        .then((v) => {
          if (!live) return;
          const m = new Map<string, number>();
          for (const r of v.parts?.rows ?? []) m.set(`${partKey(r.part)}|${r.colour}`, (m.get(`${partKey(r.part)}|${r.colour}`) ?? 0) + r.used);
          setUsed(m);
        })
        .catch(() => { if (live) setUsed(new Map()); });
    }, tick ? 400 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [buildId, tick]);
  return { lines, used };
}
