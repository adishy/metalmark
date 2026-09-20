// The account mark's whole job is to be *stable* and *distinguishable*, so those
// are what these tests assert. "Same in every session" is not something a unit
// test can observe directly; pinning the exact output is what makes the claim
// checkable, because a golden value is a statement about every session.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AccountMark, { fillFor, initialsFor } from "@/components/AccountMark";
// `?raw` reads the real stylesheet, so the contrast check below cannot drift
// from the palette. Relies on `css: true` in vite.config.ts; see the note there.
import css from "../index.css?raw";

// ---------------------------------------------------------------------------
// The palette, read rather than restated
// ---------------------------------------------------------------------------

/** The body of a top-level rule. Mirrors the helper in `src/test/setup.ts`. */
function blockFor(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rule = new RegExp(`^${escaped}\\s*\\{`, "m").exec(source);
  if (!rule) return "";
  const open = rule.index + rule[0].length - 1;
  const close = source.indexOf("}", open);
  return close === -1 ? "" : source.slice(open + 1, close);
}

function varsIn(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    out[name] = value.trim();
  }
  return out;
}

const CHANNELS = /^(\d+) (\d+) (\d+)$/;

/** WCAG 2.1 relative luminance. */
function luminance(value: string): number {
  const m = CHANNELS.exec(value);
  if (!m) throw new Error(`Not a bare channel triplet: ${JSON.stringify(value)}`);
  const [r, g, b] = [Number(m[1]), Number(m[2]), Number(m[3])].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(one: string, other: string): number {
  const [a, b] = [luminance(one), luminance(other)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

describe("AccountMark", () => {
  describe("the same account always looks the same", () => {
    it("is a pure function of the name and institution", () => {
      // Nothing in the component reads a clock, a random source or the
      // environment, and this is what says so: two independent renders of the
      // same input are byte-identical.
      const one = render(<AccountMark name="Chase Checking" institution="Chase" />);
      const first = one.container.innerHTML;
      one.unmount();
      const two = render(<AccountMark name="Chase Checking" institution="Chase" />);
      expect(two.container.innerHTML).toBe(first);
    });

    it("pins the fill for a set of real account names", () => {
      // Golden values. These are the cross-session guarantee in testable form:
      // change the hash or the curated map and they fail, which is the point —
      // an account silently changing colour is the bug worth catching.
      expect(fillFor("Chase Checking")).toBe(9);
      expect(fillFor("Chase Savings")).toBe(9);
      expect(fillFor("Amex")).toBe(2);
      expect(fillFor("Capital One Venture")).toBe(7);
      expect(fillFor("Vanguard Brokerage")).toBe(8);
      expect(fillFor("Fidelity 401k")).toBe(6);
      expect(fillFor("Zzyzx Credit Union")).toBe(5);
      expect(fillFor("Rainy Day Fund")).toBe(8);
    });

    it("ignores case and surrounding whitespace", () => {
      expect(fillFor("  chase checking ")).toBe(fillFor("Chase Checking"));
      expect(fillFor("CHASE")).toBe(fillFor("chase"));
    });

    it("re-marks an account when it is renamed, which is the only thing that moves it", () => {
      expect(fillFor("Rainy Day Fund")).not.toBe(fillFor("Rainy Day Savings"));
    });
  });

  describe("two accounts do not collide", () => {
    it("distinguishes two accounts at the same institution", () => {
      // The case the curated map cannot solve: both are Chase, so both are
      // Chase-coloured. The initials are what keep them apart, and the
      // accessible name is what makes that available to more than the eye.
      render(<AccountMark name="Chase Checking" institution="Chase" />);
      render(<AccountMark name="Chase Savings" institution="Chase" />);
      expect(screen.getByRole("img", { name: "Chase Checking" })).toHaveTextContent("CC");
      expect(screen.getByRole("img", { name: "Chase Savings" })).toHaveTextContent("CS");
      expect(fillFor("Chase Checking")).toBe(fillFor("Chase Savings"));
    });

    it("gives every account in a realistic household its own initials", () => {
      const accounts = [
        "Chase Checking",
        "Chase Savings",
        "Amex",
        "Capital One Venture",
        "Fidelity 401k",
        "Vanguard Brokerage",
        "Wells Fargo Savings",
        "Discover Card",
        "Citi Double Cash",
        "Joint Checking",
      ];
      const marks = accounts.map(initialsFor);
      expect(new Set(marks).size).toBe(accounts.length);
    });

    it("takes the second letter from the second word, not from the noise", () => {
      // "Bank of America" is BA: "of" carries no identity, and "BO" would be a
      // worse name for the account than one nobody recognises.
      expect(initialsFor("Bank of America")).toBe("BA");
      expect(initialsFor("The Home Depot Card")).toBe("HD");
    });
  });

  describe("the initials", () => {
    it("uses two letters, never one", () => {
      // One letter is the same mark for too many institutions to be worth
      // drawing, so a single-word name keeps two of its characters.
      expect(initialsFor("Amex")).toBe("AM");
      expect(initialsFor("Cash")).toBe("CA");
    });

    it("never exceeds two characters, which is what fits in 20px", () => {
      for (const name of ["Chase Checking", "Amex", "A", "", "  ", "Bank of America"]) {
        expect([...initialsFor(name)].length, name).toBeLessThanOrEqual(2);
      }
    });

    it("has something to draw even for an empty name", () => {
      expect(initialsFor("")).toBe("?");
      expect(initialsFor("   ")).toBe("?");
    });

    it("does not split a character in half", () => {
      // `slice(0, 2)` is UTF-16 units, so an astral character — an emoji in a
      // nickname — would come back as half a surrogate pair and render as a
      // replacement glyph.
      expect(initialsFor("🦋 Fund")).toBe("🦋F");
    });
  });

  describe("the curated institution map", () => {
    it("prefers the institution when one is given", () => {
      // The name alone hashes to a different token, so this is the institution
      // lookup firing and not a coincidence.
      expect(fillFor("Rainy Day Fund", "Chase")).toBe(9);
      expect(fillFor("Rainy Day Fund")).not.toBe(9);
    });

    it("matches whole words only", () => {
      // "Chasey Bank" contains "chase" and is not Chase. The padded haystack is
      // what makes that true; without it every account holding a substring of
      // an institution name would take that institution's hue.
      expect(fillFor("Chasey Bank")).toBe(1);
      expect(fillFor("Chase Checking")).toBe(9);
    });

    it("matches a multi-word institution", () => {
      expect(fillFor("Capital One Venture")).toBe(7);
      expect(fillFor("capital one")).toBe(7);
    });

    it("falls back to the hash for an institution nobody curated", () => {
      // Not an error: the mark is still deterministic, and the initials still
      // distinguish the account.
      expect(fillFor("Zzyzx Credit Union")).toBeGreaterThanOrEqual(1);
      expect(fillFor("Zzyzx Credit Union")).toBeLessThanOrEqual(10);
    });
  });

  describe("legibility (§7)", () => {
    it("is never colour alone: the initials carry the account", () => {
      // §7 item 8. The fill is a reinforcement, so a reader who cannot separate
      // two hues still has the letters and the accessible name.
      render(<AccountMark name="Rainy Day Fund" />);
      expect(screen.getByTestId("account-mark")).toHaveTextContent("RD");
      expect(screen.getByRole("img", { name: "Rainy Day Fund" })).toBeInTheDocument();
    });

    it("stays within the avatar sizes and the type floor (§2.4, §2.6)", () => {
      const { container } = render(<AccountMark name="Chase Checking" />);
      const mark = container.querySelector("[data-testid='account-mark']")!;
      expect(mark).toHaveClass("size-5"); // 20px, the size a list row can afford
      expect(mark).toHaveClass("text-xs"); // 12px — the floor, and the largest that fits
      expect(mark).toHaveClass("rounded-full"); // §2.6: avatars are circles
    });

    it("offers a 24px size for cards and sheets", () => {
      const { container } = render(<AccountMark name="Chase Checking" size="md" />);
      expect(container.querySelector("[data-testid='account-mark']")).toHaveClass("size-6");
    });

    it("meets 4.5:1 between the initials and every fill it can choose, in both themes", () => {
      // The reason all ten chart tokens are usable as solid fills. `accent-fg`
      // is the token that is defined as "text drawn on an accent fill", and it
      // flips with the theme — so this also proves the monogram stays readable
      // when the theme changes rather than only in the default.
      for (const [theme, selector] of [
        ["light", ":root"],
        ["dark", ".dark"],
      ] as const) {
        const vars = varsIn(blockFor(css, selector));
        expect(Object.keys(vars).length, `${theme} block must be readable`).toBeGreaterThan(0);
        const fg = vars["accent-fg"];
        expect(fg, `${theme} accent-fg`).toBeDefined();
        for (let n = 1; n <= 10; n += 1) {
          const fill = vars[`chart-${n}`];
          expect(fill, `${theme} chart-${n}`).toBeDefined();
          expect(contrast(fill, fg), `${theme} chart-${n} vs accent-fg`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });

    it("paints a fill that Tailwind can actually see", () => {
      // `bg-chart-${n}` would be invisible to Tailwind's scanner and render as
      // no background at all — a transparent circle with dark text in it. This
      // asserts the emitted class is one of the literal strings.
      render(<AccountMark name="Rainy Day Fund" />);
      const classNames = screen.getByTestId("account-mark").className.split(/\s+/);
      expect(classNames.filter((c) => c.startsWith("bg-chart-"))).toHaveLength(1);
    });
  });

  describe("the accessible name", () => {
    it("names the account, not the letters", () => {
      render(<AccountMark name="Chase Checking" />);
      // "C C" read out between a merchant and an amount is worse than nothing,
      // so the initials are hidden from the accessibility tree.
      expect(screen.getByRole("img", { name: "Chase Checking" })).toBeInTheDocument();
      expect(screen.getByRole("img")).toHaveAttribute("title", "Chase Checking");
    });

    it("adds the institution when the name does not already say it", () => {
      render(<AccountMark name="Rainy Day Fund" institution="Chase" />);
      expect(screen.getByRole("img", { name: "Rainy Day Fund (Chase)" })).toBeInTheDocument();
    });

    it("does not say the institution twice", () => {
      render(<AccountMark name="Chase Checking" institution="Chase" />);
      expect(screen.getByRole("img", { name: "Chase Checking" })).toBeInTheDocument();
    });

    it("copes with a null institution", () => {
      render(<AccountMark name="Rainy Day Fund" institution={null} />);
      expect(screen.getByRole("img", { name: "Rainy Day Fund" })).toBeInTheDocument();
    });
  });
});
