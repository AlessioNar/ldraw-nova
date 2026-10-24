import { useEffect, useState } from "react";
import { paletteApi } from "../api";

export type OwnedLine = { part: string; description: string; colour: number; colourName: string; owned: number; used: number };

/**
 * The person's parts (all parts lists together) that match `query`, with what the draft uses of each: one palette call
 * (inventory_only), refetched after every edit. `lines` is null while loading and `none` is true when there is no parts list (400).
 */
export function useOwned(buildId: string, query: string, tick: number) {
  const [state, setState] = useState<{ lines: OwnedLine[] | null; more: boolean; none: boolean }>({ lines: null, more: false, none: false });
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      paletteApi.search(`b-${buildId}`, { query: query.trim(), inventoryOnly: true, limit: 50 })
        .then((r) => {
          if (!live) return;
          const lines = r.items.flatMap((it) => it.colours.filter((c) => c.owned > 0).map((c) =>
            ({ part: it.part, description: it.description, colour: c.colour, colourName: c.name, owned: c.owned, used: c.used })));
          setState({ lines, more: r.more, none: false });
        })
        .catch((e: { status?: number }) => { if (live) setState({ lines: [], more: false, none: e.status === 400 }); });
    }, query || tick ? 300 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [buildId, query, tick]);
  return state;
}
