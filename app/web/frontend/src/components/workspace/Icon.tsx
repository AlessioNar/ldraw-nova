import type { ReactNode } from "react";

// Line icons from the approved mockup (docs/plans/build-workspace/mockup.html), 24 x 24, stroke = currentColor.
const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const PATHS: Record<string, ReactNode> = {
  select: <path d="M5 3l14 8-6 2 4 7-3 1-4-7-5 4z" {...S} />,
  move: <path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3" {...S} />,
  rotate: <path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5" {...S} />,
  add: <><rect x="4" y="9" width="16" height="10" rx="1.5" {...S} /><circle cx="9" cy="6.5" r="1.6" fill="currentColor" /><circle cx="15" cy="6.5" r="1.6" fill="currentColor" /></>,
  paint: <path d="M5 14l8-9 6 6-8 9H7z M17 15c1.5 2 2 3 2 4a2 2 0 0 1-4 0c0-1 .5-2 2-4z" {...S} />,
  delete: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" {...S} />,
  eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" {...S} /><circle cx="12" cy="12" r="3" {...S} /></>,
  play: <path d="M7 4l13 8-13 8z" {...S} />,
  pencil: <path d="M4 20l1-5L16 4l4 4L9 19z" {...S} />,
  undo: <path d="M9 6L4 11l5 5M4 11h9a6 6 0 0 1 0 12" {...S} transform="translate(0 -3)" />,
  redo: <path d="M15 6l5 5-5 5M20 11h-9a6 6 0 0 0 0 12" {...S} transform="translate(0 -3)" />,
  fit: <><path d="M3 9V5a2 2 0 0 1 2-2h4M15 3h4a2 2 0 0 1 2 2v4M21 15v4a2 2 0 0 1-2 2h-4M9 21H5a2 2 0 0 1-2-2v-4" {...S} /><circle cx="12" cy="12" r="3" {...S} /></>,
  check: <path d="M5 12.5l4.5 4.5L19 7" {...S} strokeWidth={2.4} />,
  warn: <path d="M12 4l9 16H3zM12 10v4M12 17v.5" {...S} strokeWidth={2} />,
  logo: <><rect x="3" y="9" width="18" height="11" rx="1.5" fill="var(--accent-solid)" /><rect x="6" y="5" width="4" height="4" fill="var(--accent-solid)" /><rect x="14" y="5" width="4" height="4" fill="var(--accent-solid)" /></>,
  download: <path d="M12 4v11M7 11l5 5 5-5M5 20h14" {...S} />,
};

export default function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">{PATHS[name]}</svg>;
}
