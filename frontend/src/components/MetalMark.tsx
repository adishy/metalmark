// The metalmark, at whatever size the caller asks for.
//
// An `<img>` of the generated file rather than the path data written a second
// time in JSX. `scripts/metalmark-mark.mjs` draws this mark once and
// `npm run icons` writes it to `public/`, so the picture in the tab, the picture
// in the launcher, the one on a desktop notification and the one beside the
// wordmark are all the same bytes. A JSX copy would be a second geometry, and
// the copy that drifts is the one nobody looks at.
//
// It also keeps the SVG's two gradient ids (`sheen`, `metal`) out of the page.
// Inlining would put an `id="sheen"` in the document per instance — the header
// and the login card would each define one, and every `url(#sheen)` in both
// would resolve to whichever the browser saw first. The two are identical today,
// which is exactly why the bug would survive until the day they were not.
//
// Nothing recolours it for dark mode, and that is deliberate. The mark is a
// filled tile because it has to be legible at 16 px in a tab strip; on the dark
// surface (slate-950) its own field (slate-900) sits one step up with a teal rim
// on it, which is how an app icon reads on a dark wallpaper. Tinting it to the
// accent would trade that for a mark that no longer matches the favicon.
//
// Decorative, always: every call site puts the word "MetalMark" beside it, so an
// alt text would say the name twice to a screen reader. That is enforced here
// rather than left to the call site, for the same reason `icons.tsx` is
// `aria-hidden` unconditionally (§4.2).
//
// `width`/`height` as attributes as well as the class, so the box is the right
// size before the image loads and the row does not shift when it arrives.

export function MetalMark({ size = 28 }: { size?: number }) {
  return (
    <img
      src="/favicon.svg"
      alt=""
      width={size}
      height={size}
      data-testid="metalmark-mark"
      className="shrink-0"
    />
  );
}
