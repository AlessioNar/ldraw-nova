import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** The modal used by the parts dialogs: backdrop, header with a close button, Escape to close (unless busy). */
export default function Dialog({ title, label, busy = false, wide = false, onClose, children }:
  { title: string; label?: string; busy?: boolean; wide?: boolean; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose, busy]);
  return createPortal(
    <div className="modal-backdrop" onClick={() => !busy && onClose()}>
      <div className={`modal parts-dialog ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={label ?? title} onClick={e => e.stopPropagation()}>
        <header className="modal-head">
          <strong className="ellipsis">{title}</strong>
          <button className="icon-button" aria-label="Close" disabled={busy} onClick={onClose}>✕</button>
        </header>
        <div className="parts-dialog-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
