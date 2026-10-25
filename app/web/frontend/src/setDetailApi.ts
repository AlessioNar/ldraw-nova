import { request } from "./api";

export type SetRow = {
  part: string; colour: number; colour_name: string; description: string; category: string;
  needed: number; owned: number; reserved: number; missing: number; on_hand: number;
};
export type OwnedSetDetail = {
  id: string; source_name: string; set_num: string; number: string; brand: string; name: string; year: number; theme: string;
  image: string; copies: number; state: "parts" | "built"; num_parts: number; dropped_pieces: number; warnings: string[];
  pieces: number; distinct_parts: number; reserved: number; missing: number; on_hand: number;
};

const sid = (id: string) => encodeURIComponent(id);

export const setDetailApi = {
  get: (id: string) => request<{ set: OwnedSetDetail; rows: SetRow[] }>(`/api/sets/${sid(id)}`),
  uploadImage: async (id: string, file: File) => {
    const res = await fetch(`/api/sets/${sid(id)}/image`, { method: "POST", body: file, headers: { "Content-Type": file.type } });
    if (!res.ok) throw new Error(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? `Upload failed (${res.status})`);
  },
  removeImage: (id: string) => request<{ removed: boolean }>(`/api/sets/${sid(id)}/image`, { method: "DELETE" }),
  /** The missing pieces as a file in the chosen wanted-list format; the browser saves it. */
  downloadWanted: async (name: string, format: string, rows: SetRow[]) => {
    const lines = rows.filter(r => r.missing > 0).map(r => ({ part: r.part, colour: r.colour, qty: r.missing }));
    const res = await fetch("/api/collection/export/lines?download=true", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format, lines, name }),
    });
    if (!res.ok) throw new Error(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? `Export failed (${res.status})`);
    const omitted = Number(res.headers.get("X-Export-Omitted") ?? 0);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? `${name}-wanted`;
    a.click();
    URL.revokeObjectURL(url);
    return omitted;
  },
};
