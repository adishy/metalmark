// Accessible modal / slide-over. Focus moves into the panel on open and returns
// to the previously-focused element on close; Escape and backdrop-click close;
// focus is trapped via a simple Tab cycle. Rendered inline (no portal needed for
// this app's single-root layout).
import { useEffect, useRef, type ReactNode } from "react";

export default function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  side = false,
  testid,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  /** Render as a right-hand slide-over instead of a centered modal. */
  side?: boolean;
  testid?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    // Move focus into the panel (first focusable, else the panel itself).
    const focusables = panelRef.current?.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    (focusables && focusables[0] ? focusables[0] : panelRef.current)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "Tab" && focusables && focusables.length > 0) {
        const list = Array.from(
          panelRef.current!.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => !el.hasAttribute("disabled"));
        if (list.length === 0) return;
        const first = list[0];
        const last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      restoreRef.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex bg-black/60"
      style={{ alignItems: side ? "stretch" : "center", justifyContent: side ? "flex-end" : "center" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid={testid ? `${testid}-overlay` : undefined}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={
          side
            ? "flex h-full w-full max-w-md flex-col bg-surface-raised shadow-2xl outline-none"
            : "m-4 flex max-h-[90vh] w-full max-w-lg flex-col rounded-card bg-surface-raised shadow-2xl outline-none"
        }
        data-testid={testid}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-control px-2 py-1 text-fg-muted hover:bg-surface-inset hover:text-fg"
            data-testid={testid ? `${testid}-close` : undefined}
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>
        )}
      </div>
    </div>
  );
}
