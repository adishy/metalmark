// A PDF, drawn page by page with pdf.js.
//
// Not the browser's own viewer in a frame: phones do not show a PDF inside a
// page at all (the frame is blank), and that is where a statement is most often
// looked at. Loaded on demand — the library is large, and most sessions never
// open a file.
import { useEffect, useRef, useState } from "react";

/** Pages drawn in place. A longer file says so and points at the full one. */
const MAX_PAGES = 40;

export default function PdfPages({ data, name }: { data: Blob; name: string }) {
  const holder = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<{ pages: number; drawn: number } | "failed" | null>(null);

  useEffect(() => {
    let cancelled = false;
    let destroy: (() => void) | undefined;
    const target = holder.current;
    setState(null);
    (async () => {
      const [pdfjs, worker] = await Promise.all([
        import("pdfjs-dist"),
        import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
      ]);
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      const task = pdfjs.getDocument({
        data: new Uint8Array(await data.arrayBuffer()),
        // A statement has no business running script or fetching anything.
        isEvalSupported: false,
        enableXfa: false,
        disableAutoFetch: true,
      });
      destroy = () => void task.destroy();
      const doc = await task.promise;
      if (cancelled || !target) return;
      const count = Math.min(doc.numPages, MAX_PAGES);
      const width = target.clientWidth || 600;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      for (let n = 1; n <= count; n += 1) {
        const page = await doc.getPage(n);
        if (cancelled) return;
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: (width / base.width) * ratio });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.className = "block w-full rounded-control border border-border bg-white";
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", `${name}, page ${n} of ${doc.numPages}`);
        target.appendChild(canvas);
        await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
        if (cancelled) return;
        setState({ pages: doc.numPages, drawn: n });
      }
    })().catch(() => {
      if (!cancelled) setState("failed");
    });
    return () => {
      cancelled = true;
      destroy?.();
      target?.replaceChildren();
    };
  }, [data, name]);

  return (
    <div data-testid="pdf-pages">
      {state === null && <p role="status" className="text-sm text-fg-muted">Opening…</p>}
      {state === "failed" && (
        <p role="alert" className="text-sm text-negative">
          This PDF could not be shown here. Open it in a new tab or download it instead.
        </p>
      )}
      <div ref={holder} className="space-y-3" />
      {state && state !== "failed" && state.pages > MAX_PAGES && state.drawn === MAX_PAGES && (
        <p className="mt-2 text-xs text-fg-muted">
          The first {MAX_PAGES} of {state.pages} pages. Open it in a new tab or download it to see
          the rest.
        </p>
      )}
    </div>
  );
}
