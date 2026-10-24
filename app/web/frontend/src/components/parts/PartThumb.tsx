import { useEffect, useRef, useState } from "react";

/** One neutral picture per part type, rendered by the backend on first view and cached there. Until it exists (or when it
 *  cannot be made) a plain brick outline stands in. Loading is lazy (only parts on screen) and limited to a few at a time. */
const urls = new Map<string, string | null>();
const inflight = new Map<string, Promise<string | null>>();
const waiting: (() => void)[] = [];
let running = 0;
const MAX_PARALLEL = 3;
const RETRY_MS = 2500;
const ATTEMPTS = 12;

async function slot<T>(work: () => Promise<T>): Promise<T> {
  if (running >= MAX_PARALLEL) await new Promise<void>(resolve => waiting.push(resolve));
  running++;
  try { return await work(); } finally { running--; waiting.shift()?.(); }
}

async function load(part: string): Promise<string | null> {
  for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
    const res = await slot(() => fetch(`/api/parts/${encodeURIComponent(part)}/thumb`));
    if (res.status === 200) return URL.createObjectURL(await res.blob());
    if (res.status !== 202 || res.headers.get("X-Thumb-Status") === "failed") return null;
    await new Promise(r => setTimeout(r, RETRY_MS));
  }
  return null;
}

function fetchThumb(part: string): Promise<string | null> {
  if (urls.has(part)) return Promise.resolve(urls.get(part) ?? null);
  let job = inflight.get(part);
  if (!job) {
    job = load(part).catch(() => null).then(url => { urls.set(part, url); inflight.delete(part); return url; });
    inflight.set(part, job);
  }
  return job;
}

export function BrickIcon({ size }: { size: number }) {
  return <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 48 48" aria-hidden fill="none" stroke="currentColor" strokeWidth="2.5">
    <rect x="7" y="22" width="34" height="17" rx="2.5" />
    <rect x="13" y="14" width="8" height="8" rx="1.5" />
    <rect x="27" y="14" width="8" height="8" rx="1.5" />
  </svg>;
}

export default function PartThumb({ part, size = 52 }: { part: string; size?: number }) {
  const box = useRef<HTMLSpanElement>(null);
  const [url, setUrl] = useState<string | null>(urls.get(part) ?? null);
  const [visible, setVisible] = useState(urls.has(part));

  useEffect(() => {
    setUrl(urls.get(part) ?? null);
    setVisible(urls.has(part));
  }, [part]);
  useEffect(() => {
    if (visible || !box.current) return;
    const watcher = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) { setVisible(true); watcher.disconnect(); } }, { rootMargin: "200px" });
    watcher.observe(box.current);
    return () => watcher.disconnect();
  }, [visible, part]);
  useEffect(() => {
    if (!visible || url) return;
    let alive = true;
    void fetchThumb(part).then(u => alive && setUrl(u));
    return () => { alive = false; };
  }, [visible, part, url]);

  return <span ref={box} className="part-thumb" style={{ width: size, height: size }} title={url ? undefined : "Picture not ready yet"}>
    {url ? <img src={url} alt="" width={size} height={size} loading="lazy" /> : <BrickIcon size={size} />}
  </span>;
}
