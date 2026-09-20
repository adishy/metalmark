/*
 * The metalmark, as SVG text.
 *
 * MetalMark is named for the metalmark butterfly (Riodinidae), so the mark is a
 * butterfly and the design hook is the family's *metallic* banding — here a
 * bright-to-deep sheen across each wing rather than a flat fill, which is what
 * stops it reading as a generic clip-art butterfly.
 *
 * Pure and dependency-free: it emits a string, and `generate-icons.mjs` is the
 * only thing that knows how to turn one into pixels. If the rasteriser is ever
 * swapped out, this file does not change.
 *
 * ---------------------------------------------------------------------------
 * Why it is drawn the way it is
 * ---------------------------------------------------------------------------
 *
 * The hard constraint is 16px in a browser tab. At that size the whole mark is
 * 16 px and the butterfly is ~10 px across, so the only thing that can survive
 * is a bold silhouette plus one strong light/dark relationship. Everything here
 * follows from that:
 *
 *   - **A filled field, not a transparent drawing.** A transparent mark
 *     disappears into whatever chrome it is sitting on. The field is `#0f172a`
 *     — the colour the PWA manifest already declares as `background_color`, so
 *     the icon matches the install surface rather than looking like a sticker
 *     on it.
 *   - **One rule for colour: anything over the field is bright, anything over
 *     the wings is dark.** The body is only legible because it is the field
 *     colour cutting through two bright wings; the head and antennae are only
 *     legible because they sit above the wings, on the field, and are bright.
 *     Swap either and that part vanishes.
 *   - **Wings meet the body, never near-miss it.** A gap between wing and body
 *     closes up at 16px and the butterfly becomes two blobs.
 *   - Detail that only exists at large sizes is deliberate and confined to the
 *     interior of the wings, where its worst case at 16px is a slightly
 *     textured highlight rather than a broken edge.
 *
 * Colours are the app's own brand teals rather than a new palette:
 * `#14b8a6` is where the dark theme's `--accent` sits, the near-white is where
 * the sheen starts, and the veins are the field colour — the same navy that
 * cuts the body draws the banding, so there is one dark in the drawing and not
 * two. An icon is not a `.tsx` file, so these are literals, but they are the
 * same literals `src/index.css` defines.
 */

export const FIELD = "#0f172a"; // == the manifest's background_color
export const METAL_LIGHT = "#f0fdfa";
export const METAL_MID = "#99f6e4";
export const METAL_DEEP = "#14b8a6";
export const BRIGHT = "#5eead4";
export const VEIN = FIELD;

/** The tile's corner radius and the bright rim that keeps the field's edge
 *  visible against dark browser chrome. */
const TILE_RADIUS = 14;
const RIM_WIDTH = 2;
const RIM_OPACITY = 0.45;

/** Maskable icons are cropped to a circle of 80% of the canvas, so the artwork
 *  is scaled into that safe zone and only the field runs to the edges. */
const MASKABLE_SAFE_SCALE = 0.84;

/*
 * Geometry. One coordinate space, 64×64, and the left half is the right half
 * mirrored — `mirror()` below — so the butterfly cannot come out lopsided.
 *
 * A subpath is a start point plus a list of cubic segments; lines are cubics
 * with their control points laid on the line, which keeps the emitter and any
 * future flattening to one case instead of three.
 */
const FOREWING = {
  start: [33, 25],
  curves: [
    [42, 16, 51, 15, 55, 19], // leading edge, out to the tip
    [53, 25, 44, 32, 33, 34], // trailing edge, back to the thorax
  ],
};

const HINDWING = {
  start: [33, 33],
  curves: [
    [42, 34, 48, 36, 49, 40],
    [49, 46, 42, 50, 32.5, 49],
  ],
};

/* The body runs from the thorax to the abdomen tip. Its top overlaps the head,
 * so the head reads as a bright cap on a dark column rather than a bead on a
 * stalk. */
const BODY = {
  start: [32, 23.5],
  curves: [
    [33.6, 24.5, 34.3, 27, 34.3, 30],
    [34.3, 39, 34.3, 42, 34.3, 44.5],
    [34.3, 48, 33.2, 50.5, 32, 51],
    [30.8, 50.5, 29.7, 48, 29.7, 44.5],
    [29.7, 42, 29.7, 39, 29.7, 30],
    [29.7, 27, 30.4, 24.5, 32, 23.5],
  ],
};

const HEAD = { cx: 32, cy: 21.6, r: 2.6 };

const ANTENNAE = [
  [
    [30.8, 19.8],
    [25.6, 13.2],
  ],
  [
    [33.2, 19.8],
    [38.4, 13.2],
  ],
];

/** The metallic banding. Interior only, so its small-size failure mode is a
 *  little texture rather than a chewed edge. */
const VEINS = [
  { start: [36.5, 26.5], curves: [[42, 24, 48, 22, 52.5, 21]] },
  { start: [36, 39.5], curves: [[40, 39, 44, 38.5, 47.5, 38]] },
];

const n = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100));

/** A subpath as a `d` fragment. */
function pathOf({ start, curves }) {
  const parts = [`M${n(start[0])} ${n(start[1])}`];
  for (const [c1x, c1y, c2x, c2y, x, y] of curves) {
    parts.push(`C${n(c1x)} ${n(c1y)} ${n(c2x)} ${n(c2y)} ${n(x)} ${n(y)}`);
  }
  return `${parts.join("")}Z`;
}

/** The same subpath reflected across x = 32. Written by transforming the
 *  control points, not by a `scale(-1,1)` group, so the output stays one shape
 *  list with no nested transforms for the eye to skip over. */
function mirror({ start, curves }) {
  const flip = ([x, y]) => [64 - x, y];
  return {
    start: flip(start),
    curves: curves.map((c) => [...flip([c[0], c[1]]), ...flip([c[2], c[3]]), ...flip([c[4], c[5]])]),
  };
}

function wing(shape, fill, extra = "") {
  return `<path d="${pathOf(shape)}" fill="${fill}"${extra}/>`;
}

/**
 * The mark as an SVG document.
 *
 * @param {{ maskable?: boolean, bleed?: boolean, size?: number }} [options]
 *   `maskable` insets the artwork into the safe zone the platform's mask
 *   guarantees, so no wing tip is ever cropped. `bleed` squares off the field
 *   instead of rounding it, for surfaces that apply their own mask (iOS, and
 *   anything maskable). `size` only sets the root width/height; the geometry is
 *   resolution-independent.
 */
export function markSvg({ maskable = false, bleed = false, size = 64 } = {}) {
  const scale = maskable ? MASKABLE_SAFE_SCALE : 1;
  const inset = (56 - 56 * scale) / 2; // 56 = the 64 box less a 4-unit margin
  const radius = bleed ? 0 : TILE_RADIUS;
  const innerRadius = bleed ? 0 : TILE_RADIUS - 1;

  const art = [
    wing(mirror(FOREWING), "url(#sheen)"),
    wing(FOREWING, "url(#sheen)"),
    wing(mirror(HINDWING), "url(#metal)"),
    wing(HINDWING, "url(#metal)"),
    ...VEINS.flatMap((v) => [
      `<path d="${pathOf(v)}" fill="none" stroke="${VEIN}" stroke-opacity="0.34" stroke-width="1.8" stroke-linecap="round"/>`,
      `<path d="${pathOf(mirror(v))}" fill="none" stroke="${VEIN}" stroke-opacity="0.34" stroke-width="1.8" stroke-linecap="round"/>`,
    ]),
    wing(BODY, FIELD),
    `<circle cx="${HEAD.cx}" cy="${HEAD.cy}" r="${HEAD.r}" fill="${FIELD}"/>`,
    `<circle cx="${HEAD.cx}" cy="${HEAD.cy}" r="${HEAD.r}" fill="${BRIGHT}" fill-opacity="0.35"/>`,
    ...ANTENNAE.flatMap(([a, b]) => [
      `<path d="M${n(a[0])} ${n(a[1])}L${n(b[0])} ${n(b[1])}" fill="none" stroke="${BRIGHT}" stroke-width="2" stroke-linecap="round"/>`,
      `<circle cx="${n(b[0])}" cy="${n(b[1])}" r="1.7" fill="${BRIGHT}"/>`,
    ]),
  ].join("\n      ");

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 64 64" role="img" aria-label="MetalMark">
  <defs>
    <!-- The sheen: bright at the leading edge, deep at the trailing one. Per
         wing, so each gets its own ramp and the wings read as separate planes
         of metal rather than one sheet. -->
    <linearGradient id="sheen" x1="0.1" y1="0" x2="0.45" y2="1">
      <stop offset="0" stop-color="${METAL_LIGHT}"/>
      <stop offset="0.45" stop-color="${METAL_MID}"/>
      <stop offset="1" stop-color="${METAL_DEEP}"/>
    </linearGradient>
    <linearGradient id="metal" x1="0.2" y1="0" x2="0.5" y2="1">
      <stop offset="0" stop-color="${METAL_MID}"/>
      <stop offset="1" stop-color="${METAL_DEEP}"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="${radius}" fill="${FIELD}"/>
  <rect x="1" y="1" width="62" height="62" rx="${innerRadius}" fill="none"
        stroke="${METAL_DEEP}" stroke-opacity="${RIM_OPACITY}" stroke-width="${RIM_WIDTH}"/>
  <g transform="translate(${n(inset)} ${n(inset)}) scale(${scale})">
      ${art}
  </g>
</svg>
`;
}
