// Which account a credit or debit came from, at a glance, without phoning
// anyone.
//
// The request was real institution favicons. Fetching one means either a
// third-party favicon service or a request per institution, and both leak which
// banks this household uses — the same ADR-0002 problem as Web Push, with a
// trademark question on top (decision G). So the mark is computed instead: no
// network, no third party, and the same account always produces the same mark,
// in every session, on every machine, because nothing here reads a clock, a
// random source, or the environment.
//
// What it carries, in order of how much work each does:
//
//   - **Initials**, from the account's own name, so two accounts at one
//     institution stay apart ("Chase Checking" → CC, "Chase Savings" → CS).
//     This is the signal; the colour only reinforces it, because colour alone
//     is never a distinction (DESIGN.md §7 item 8).
//   - **A colour** from the chart/series tokens, chosen by a curated hue for a
//     handful of well-known institutions and by a hash of the name otherwise.
//   - **The account name**, accessibly: `role="img"` plus an `aria-label`, and a
//     `title` for the pointer. The initials themselves are `aria-hidden`, so a
//     screen reader hears "Chase Checking" rather than "C C".
//
// It is small because it lives in a list row: 20 or 24 px, the sizes §2.6 gives
// avatars, with the initials at `text-xs` — the floor §2.4 sets, which is also
// the largest that fits inside 20 px with two letters.
import { useState, type CSSProperties } from "react";
import { useInstitutionLogo } from "@/components/InstitutionLogos";

/**
 * The fills, as literal class strings rather than `bg-chart-${n}`.
 *
 * Tailwind's scanner reads source text, so an interpolated class name is never
 * generated and the mark would silently render transparent. Writing the ten out
 * is what makes them real.
 */
export const FILLS = [
  "bg-chart-1",
  "bg-chart-2",
  "bg-chart-3",
  "bg-chart-4",
  "bg-chart-5",
  "bg-chart-6",
  "bg-chart-7",
  "bg-chart-8",
  "bg-chart-9",
  "bg-chart-10",
] as const;

export type AccountMarkFill = (typeof FILLS)[number];

/**
 * A curated hue for institutions people actually have. Matched as whole words
 * against the institution (preferred) or the account name, so "Chase Checking"
 * is Chase-coloured whether or not the `institution` column was filled in.
 *
 * The hue is the institution's *family* — Chase is blue, Capital One is red,
 * Fidelity is green — and not its trademark, which is why this is a handful of
 * entries pointing at ten tokens rather than a colour-matching exercise. Two
 * institutions can share a token; their initials still differ, and the record
 * that this is approximate is what keeps it honest.
 */
const INSTITUTIONS: Record<string, number> = {
  chase: 9, // blue
  "american express": 2, // sky
  amex: 2,
  citibank: 3, // indigo
  citi: 3,
  "capital one": 7, // red
  "bank of america": 7,
  "wells fargo": 10, // orange
  discover: 5, // amber
  fidelity: 6, // green
  vanguard: 8, // violet
};

/** Words that carry no identity, so "Bank of America" initials to BA and not
 *  BO. Kept to the ones that actually appear in institution names. */
const NOISE = new Set(["the", "of", "and", "for", "a", "an", "my", "our"]);

/** Split on anything that separates words in an account name. */
const WORD_SPLIT = /[\s\-_/&+,.]+/;

function normalise(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * FNV-1a, written out rather than taken from a library.
 *
 * It has to be *stable*, not good: the mark is a label, and this is the only
 * requirement it has to meet. `Math.imul` keeps the multiply in 32-bit integer
 * arithmetic, which is what makes the result identical in every engine — a hash
 * built from `*` alone would go through a double and disagree at the edges.
 */
function hash32(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** Up to two characters standing for the name. Two words give their first
 *  letters; one word gives its first two, so "Amex" is AM and not A — one
 *  letter is the same mark for too many institutions to be worth drawing. */
export function initialsFor(name: string): string {
  const words = name.split(WORD_SPLIT).filter((w) => w.length > 0);
  const meaningful = words.filter((w) => !NOISE.has(w.toLowerCase()));
  const use = meaningful.length > 0 ? meaningful : words;
  if (use.length === 0) return "?";
  // Spread first: `slice(0, 2)` on a word is UTF-16 units, and an astral
  // character (an emoji in a nickname, a rare script) would be cut in half.
  const chars = [...use[0]];
  if (use.length === 1) return chars.slice(0, 2).join("").toUpperCase();
  const second = [...use[1]];
  return (chars[0] + (second[0] ?? "")).toUpperCase();
}

/** The token index (1-10) this account's fill comes from. */
export function fillFor(name: string, institution?: string | null): number {
  const keys = [normalise(institution ?? ""), normalise(name)].filter(Boolean);
  for (const key of keys) {
    // Padded so the match is a whole word or phrase: "chasey bank" is not
    // Chase, and "bank of america" has to beat "bank".
    const haystack = ` ${key} `;
    for (const [token, fill] of Object.entries(INSTITUTIONS)) {
      if (haystack.includes(` ${token} `)) return fill;
    }
  }
  return (hash32(normalise(name)) % FILLS.length) + 1;
}

const SIZES = {
  /** 20 px — the size a list row can afford. */
  sm: "size-5",
  /** 24 px — for a card or a sheet, where the row is not sharing space. */
  md: "size-6",
  /** 40 px — a list that leads with the account, like the Accounts page, where
   *  a logo has to be big enough to recognise. */
  lg: "size-10 text-sm",
} as const;

export interface AccountMarkProps {
  /** The account's own name. This is the identity: both the initials and the
   *  hash come from it, so renaming an account re-marks it and nothing else
   *  does. */
  name: string;
  /** The institution, when the account has one. Used for the curated hue and
   *  named in the accessible label only if the name does not already say it. */
  institution?: string | null;
  size?: keyof typeof SIZES;
  className?: string;
  style?: CSSProperties;
}

export default function AccountMark({
  name,
  institution,
  size = "sm",
  className,
  style,
}: AccountMarkProps) {
  const fill = FILLS[fillFor(name, institution) - 1] ?? FILLS[0];
  // The household's logo for the institution, served by this server (never
  // fetched from the web by the browser — §4.15). A logo that fails to load
  // falls back to the initials rather than a broken-image glyph.
  const logo = useInstitutionLogo(institution);
  const [broken, setBroken] = useState(false);
  const inst = (institution ?? "").trim();
  // "Chase Checking (Chase)" is noise; only add the institution when the name
  // does not already contain it.
  const label =
    inst && !normalise(name).includes(normalise(inst)) ? `${name} (${inst})` : name;

  return (
    <span
      // An image with a name, not text: the two letters are a drawing of the
      // account, and "C C" read out between the merchant and the amount is
      // worse than nothing.
      role="img"
      aria-label={label}
      title={label}
      style={style}
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full text-xs leading-none font-semibold ${
        logo && !broken ? "border border-border bg-white" : `text-accent-fg ${fill}`
      } ${SIZES[size]} ${className ?? ""}`}
      data-testid="account-mark"
    >
      {logo && !broken ? (
        // A logo is drawn on white in both themes: logos are designed for a
        // light ground, and a dark one turns most of them into a smudge.
        <img
          src={logo}
          alt=""
          aria-hidden="true"
          className="size-full object-contain p-0.5"
          onError={() => setBroken(true)}
          data-testid="account-mark-logo"
        />
      ) : (
        <span aria-hidden="true">{initialsFor(name)}</span>
      )}
    </span>
  );
}
