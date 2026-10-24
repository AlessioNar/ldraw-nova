import { useEffect, useState } from "react";
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
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <header className={`appbar${open ? " open" : ""}`}>
      <Link to="/" className="brand-home" title="Home"><Logo /><span className="brand-name">Open Brick Designer</span></Link>
      <button className="appbar-toggle icon-button" aria-label="Menu" aria-expanded={open} aria-controls="appbar-nav" onClick={() => setOpen(!open)}>{open ? "✕" : "☰"}</button>
      <nav id="appbar-nav" className="appbar-nav" aria-label="Main">
        {LINKS.map((l) => <NavLink key={l.to} to={l.to}>{l.label}</NavLink>)}
      </nav>
    </header>
  );
}
