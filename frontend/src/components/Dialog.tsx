// Accessible modal / bottom sheet / slide-over. Focus moves into the panel on
// open and returns to the previously-focused element on close; Escape and
// backdrop-tap close; focus is trapped via a Tab cycle. Rendered inline (no
// portal needed for this app's single-root layout).
//
// One component, three presentations (DESIGN.md §4.8):
//   <640px  bottom sheet, flush to the bottom edge, drag-to-dismiss
//   >=640px centred modal
//   side    right slide-over, for detail views on wide screens
import { useEffect, useId, useRef, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { useIsPhone } from "@/lib/media";
import { CloseIcon } from "@/components/icons";

/** Past this drag distance (or flick speed) a sheet dismisses. */
const DISMISS_PX = 120;
const DISMISS_VELOCITY = 800;

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
  /** Render as a right-hand slide-over instead of a centred modal. */
  side?: boolean;
  testid?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const isPhone = useIsPhone();
  const reduced = useReducedMotion();

  /*
   * `onClose` is almost always an inline arrow (`onClose={() => setOpen(false)}`),
   * so its identity changes on every parent render. In the effect's dependency
   * array that re-runs the whole effect each time — which re-moves focus to the
   * first control and runs the cleanup, restoring focus to whatever was focused
   * when the dialog opened. The visible symptom is focus jumping out of the
   * field the user is typing in, mid-keystroke, every time the parent re-renders.
   * Holding it in a ref keeps the effect keyed on `open` alone.
   */
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;

    const focusables = panelRef.current?.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    (focusables && focusables[0] ? focusables[0] : panelRef.current)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === "Tab") {
        const list = Array.from(
          panelRef.current?.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          ) ?? [],
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
  }, [open]);

  // A background that scrolls under a sheet is how a user taps the wrong row.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;

  // Drag is a sheet gesture only: on a centred modal it just makes the panel
  // wobble. Reduced-motion users get no drag at all rather than an unanimated one.
  const draggable = isPhone && !side && !reduced;

  return (
    <div
      className={`fixed inset-0 z-50 flex bg-black/60 ${
        side ? "items-stretch justify-end" : "items-end justify-center sm:items-center"
      }`}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid={testid ? `${testid}-overlay` : undefined}
    >
      <motion.div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        // Labelled by the visible heading rather than a duplicate string, so the
        // accessible name cannot drift from what is on screen.
        aria-labelledby={titleId}
        tabIndex={-1}
        drag={draggable ? "y" : false}
        dragConstraints={{ top: 0, bottom: 0 }}
        dragElastic={{ top: 0, bottom: 0.5 }}
        dragSnapToOrigin
        onDragEnd={(_e, info) => {
          if (info.offset.y > DISMISS_PX || info.velocity.y > DISMISS_VELOCITY) onClose();
        }}
        className={
          // shadow-lg, not shadow-2xl: §2.6 allows exactly one overlay shadow.
          // No `outline-none`: the panel is `tabIndex={-1}` and receives focus
          // when it has no focusable children, and that focus must be visible.
          // The global `:focus-visible` rule supplies the ring.
          "relative flex flex-col bg-surface-raised shadow-lg " +
          (side
            ? "h-full w-full max-w-md"
            : // `dvh`, not `vh`: on iOS Safari `vh` is measured against the
              // largest possible viewport, so an 85vh sheet overflows the screen
              // while the URL bar is showing — exactly when it matters.
              "max-h-[85dvh] w-full max-w-lg rounded-t-overlay sm:m-4 sm:max-h-[90vh] sm:rounded-overlay")
        }
        data-testid={testid}
      >
        {/* Grab handle. Decorative: the sheet is also dismissible by the close
            button and by Escape, so this is an affordance, not the only route. */}
        {!side && (
          <div
            aria-hidden="true"
            className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-border-strong sm:hidden"
          />
        )}
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <h2 id={titleId} className="text-base font-semibold text-fg">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 grid size-11 shrink-0 place-items-center rounded-control text-fg-muted hover:bg-surface-inset hover:text-fg"
            data-testid={testid ? `${testid}-close` : undefined}
          >
            <CloseIcon />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:pb-3">
            {footer}
          </div>
        )}
      </motion.div>
    </div>
  );
}
