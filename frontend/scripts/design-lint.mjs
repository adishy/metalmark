#!/usr/bin/env node
/*
 * The §8 conformance greps from docs/DESIGN.md, as an executable check.
 *
 * The spec lists these as shell one-liners that "must return nothing". Two of
 * them cannot be taken literally:
 *
 *   - #6 has a documented exception (a badge that carries its own padding and
 *     radius may legitimately have a `bg-*` on a span), so the naive grep fires
 *     on correct code. The exception is mechanised below rather than dropped,
 *     because "the grep is noisy so we ignore it" is how a check dies.
 *   - #2 and #4 are eyeball checks in one-liner form; they are kept line-based
 *     on purpose, since the thing being caught is one bad class string.
 *
 * #7 is deliberately not implemented — the spec explains why.
 *
 * Usage: node scripts/design-lint.mjs [--quiet]
 * Exits 1 if anything is reported.
 */
import { readFileSync } from "node:fs";
import { readdir } from "node:fs/promises";
import { join, relative, extname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const SRC = join(ROOT, "src");

const PALETTE =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|" +
  "cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PREFIX = "bg|text|border|ring|divide|from|to|via|placeholder|outline";
const TOKENS =
  "surface|accent|positive|negative|warning|danger|fg|border";
const MONEY = /amount|balance|total|money|price/i;

const RULES = [
  {
    id: "1",
    why: "raw palette class — use a token",
    test: (line) => new RegExp(`\\b(?:${PREFIX})-(?:${PALETTE})-[0-9]{2,3}\\b`).test(line),
  },
  {
    id: "2",
    why: "a money figure must never be truncated (§6.5)",
    test: (line) => line.includes("truncate") && MONEY.test(line),
  },
  {
    id: "3",
    why: "arbitrary hex or px — env()/calc()/vh are allowed, hex and px are not",
    test: (line) =>
      new RegExp(`\\b(?:text|bg|border|ring|divide)-\\[#`).test(line) ||
      new RegExp(`\\b(?:p|m|gap|space-[xy])-\\[[0-9.]+(?:px|rem)`).test(line),
  },
  {
    id: "4",
    why: "outline-none with no replacement ring (§7 item 3)",
    test: (line) => line.includes("outline-none") && !line.includes("ring-"),
  },
  {
    id: "5",
    why: "hardcoded hex in a chart option — read it from chartTokens()",
    // Scoped to the files that build chart options. Not all of `src/pages/`:
    // Settings.tsx holds hex legitimately, as the *default colour of a new
    // category*, which is user data rather than a theme value.
    files: (f) =>
      f.endsWith("src/pages/Reports.tsx") ||
      f.endsWith("src/pages/DesignSystem.tsx") ||
      f.endsWith("src/components/Chart.tsx"),
    test: (line) => /#[0-9a-fA-F]{6}\b/.test(line),
  },
  {
    id: "6",
    why: "bg-* on an inline text element — that is a text colour that got substituted",
    // The documented exception: a badge carrying its own padding AND radius.
    test: (line) => {
      const m = line.match(/<(?:p|span|h[1-6]|legend)\b[^>]*className="([^"]*)"/);
      if (!m) return false;
      const cls = m[1];
      if (!new RegExp(`\\bbg-(?:${TOKENS})\\b`).test(cls)) return false;
      const padded = /\b(?:p|px|py)-[0-9a-z[\]]/.test(cls);
      const rounded = /\brounded\b/.test(cls);
      return !(padded && rounded);
    },
  },
  {
    id: "8",
    why: "hand-written chart interaction — build it with chartTooltip()/emphasis*()/chartAxis() (§2.10)",
    // The shared module is the standard, so it is the one place allowed to name
    // these keys. Everywhere else must go through it, which is what makes the
    // interaction identical across charts instead of remembered per chart.
    files: (f) => f !== "src/theme/chartInteraction.ts",
    /*
     * Keyed on the *key*, not the string: a chart option that mentions
     * `emphasis` in a comment or a variable name is fine, whereas
     * `emphasis: { ... }` is the thing that drifts from chart to chart.
     *
     * The lookaheads consume their own whitespace (`(?!\s*chartTooltip\()`).
     * Writing `tooltip:\s*(?!chartTooltip\()` would never fire: the `\s*` and
     * the lookahead backtrack against each other, and the check ends up
     * inspecting the space rather than the call.
     */
    test: (line) =>
      /\btooltip:(?!\s*chartTooltip\()/.test(line) ||
      /\baxisPointer:/.test(line) ||
      // The alternation is the list of helpers the module offers, so a new one
      // has to be added here when it is written — the rule is "go through
      // `chartInteraction`, not "do not write `emphasis`", and only the module
      // knows which of the two a line is doing.
      /\bemphasis:(?!\s*emphasis(?:Line|Bar|Pie|Sankey)\()/.test(line),
  },
  {
    id: "9",
    why: "an ISO date as visible text — render it through <Day>/<Instant> (§9.5)",
    /*
     * Keyed on the two shapes of an ISO date that reach the screen, not on
     * `.slice(0, 10)` itself: a native `<input type="date">` needs an ISO
     * `value`, and that is a wire format rather than text anyone reads. What is
     * banned is `.slice(0, 10)` *interpolated into a string* or standing alone
     * as a JSX child — `2026-01-01 to 2026-01-31` printed at the reader, which
     * is what the OFX preview did.
     */
    test: (line) =>
      /\$\{[^}]*\.slice\(0, ?10\)/.test(line) ||
      />\{[^}]*\.slice\(0, ?10\)\}</.test(line),
  },
];

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(full)));
    else if ([".ts", ".tsx"].includes(extname(entry.name))) out.push(full);
  }
  return out;
}

/*
 * Strip comments before testing. Without this the rules fire on prose that
 * *describes* the thing being banned — a comment explaining why `outline-none`
 * is absent trips rule 4. A check that cries wolf on correct code is a check
 * that gets switched off (§8 rule 7 makes the same argument about rule 7), so
 * the comment is removed rather than the finding suppressed.
 *
 * `//` is only treated as a comment when it is not part of a `://` protocol,
 * so a URL in a string survives.
 */
function codeOnly(line, inBlock) {
  let out = "";
  let i = 0;
  while (i < line.length) {
    if (inBlock) {
      const end = line.indexOf("*/", i);
      if (end === -1) return { code: out, inBlock: true };
      i = end + 2;
      inBlock = false;
      continue;
    }
    if (line.startsWith("/*", i)) {
      inBlock = true;
      i += 2;
      continue;
    }
    if (line.startsWith("//", i) && line[i - 1] !== ":") break;
    out += line[i++];
  }
  return { code: out, inBlock };
}

const findings = [];
for (const file of await walk(SRC)) {
  const rel = relative(ROOT, file);
  const lines = readFileSync(file, "utf8").split("\n");
  // JSX comments (`{/* … */}`) arrive as block comments, so the state machine
  // covers them too.
  let inBlock = false;
  lines.forEach((line, i) => {
    const stripped = codeOnly(line, inBlock);
    inBlock = stripped.inBlock;
    for (const rule of RULES) {
      if (rule.files && !rule.files(rel)) continue;
      if (rule.test(stripped.code)) {
        findings.push({ rule: rule.id, why: rule.why, at: `${rel}:${i + 1}`, line: line.trim() });
      }
    }
  });
}

if (findings.length === 0) {
  console.log("design-lint: clean (DESIGN.md §8 rules 1-6, 8, 9)");
  process.exit(0);
}

const quiet = process.argv.includes("--quiet");
console.error(`design-lint: ${findings.length} finding(s)\n`);
for (const f of findings) {
  console.error(`  §8.${f.rule}  ${f.at}\n      ${f.why}`);
  if (!quiet) console.error(`      ${f.line.slice(0, 160)}\n`);
}
process.exit(1);
