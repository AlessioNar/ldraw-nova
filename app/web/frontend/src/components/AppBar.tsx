import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import Logo from "./Logo";

const LINKS = [
  { to: "/builds", label: "My Builds" },
  { to: "/parts", label: "My Parts" },
  { to: "/settings", label: "Settings" },
];

/** The slim top bar of every page but the build workspace (which has its own): logo and name home, the three lists on the right. */
export default function AppBar() {
  const [open, setOpen] = useState(false);
  const bar = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {      // the small-screen menu closes on Escape or a press anywhere outside the bar
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (!bar.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  return (
    <header ref={bar} className={`appbar${open ? " open" : ""}`}>
      <Link to="/" className="brand-home" title="Home"><Logo /><span className="brand-name">Open Brick Designer</span></Link>
      <button className="appbar-toggle icon-button" aria-label="Menu" aria-expanded={open} aria-controls="appbar-nav" onClick={() => setOpen(!open)}>{open ? "✕" : "☰"}</button>
      <nav id="appbar-nav" className="appbar-nav" aria-label="Main">
        {LINKS.map((l) => <NavLink key={l.to} to={l.to}>{l.label}</NavLink>)}
      </nav>
    </header>
  );
}
