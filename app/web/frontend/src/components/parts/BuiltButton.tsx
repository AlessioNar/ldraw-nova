import { useState } from "react";
import { reservationsApi, type Reservation, type Shortage } from "../../api";
import ShortageDialog from "./ShortageDialog";

/** "I built this" on a model card: holds the model's parts back from the collection, shows what was short, and releases them again. */
export default function BuiltButton({ file, title, reservation, onChanged }: { file: string; title: string; reservation?: Reservation; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [shown, setShown] = useState<{ report?: Shortage } | null>(null);

  async function run(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); onChanged(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    {reservation
      ? <>
        <button type="button" disabled={busy} onClick={() => setShown({})} title="What this model needs that is not free">
          Built{reservation.short_pieces > 0 ? ` · short ${reservation.short_pieces}` : ""}</button>
        <button type="button" disabled={busy} onClick={() => void run(async () => { await reservationsApi.release(reservation.id); })} title="Take it apart: its parts are free again">
          Take apart</button>
      </>
      : <button type="button" disabled={busy} onClick={() => void run(async () => { const r = await reservationsApi.reserve(file); setShown({ report: r.shortage }); })}
        title="Hold this model's parts back from your collection">I built this</button>}
    {error && <span className="warn-text small" role="alert">{error}</span>}
    {shown && <ShortageDialog model={file} title={`Parts for ${title}`} initial={shown.report} onClose={() => setShown(null)} />}
  </>;
}
