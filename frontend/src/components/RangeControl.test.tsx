import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import RangeControl, { useReportRange } from "@/components/RangeControl";
import { resolvePreset } from "@/lib/reportRange";

/**
 * The control and its state, wired the way `Overview` (Insights' report tab)
 * wires them.
 *
 * What this file is for: the **URL round-trip**. The arithmetic of a preset is
 * pinned against fixed dates in `reportRange.test.ts`, and the harness here
 * cannot pin "today" (the hook reads the clock at mount, which is the point of
 * it), so every assertion below is about what a press writes and reads back
 * rather than about which days a quarter covers.
 */
function Harness() {
  const state = useReportRange();
  const loc = useLocation();
  return (
    <>
      <RangeControl state={state} />
      <output data-testid="state">
        {JSON.stringify({
          mode: state.mode,
          start: state.start,
          end: state.end,
          granularity: state.granularity,
        })}
      </output>
      <output data-testid="url">{loc.pathname + loc.search}</output>
    </>
  );
}

function renderAt(search = "") {
  render(
    <MemoryRouter initialEntries={[`/insights/overview${search}`]}>
      <Harness />
    </MemoryRouter>,
  );
  return {
    url: () => screen.getByTestId("url").textContent ?? "",
    state: () =>
      JSON.parse(screen.getByTestId("state").textContent ?? "{}") as {
        mode: string;
        start: string | null;
        end: string;
        granularity: string;
      },
  };
}

/** A native date input takes its value as a whole ISO day; there is nothing to
 *  type at, so this is a `change` rather than a keystroke sequence. */
const setDate = (testid: string, value: string) =>
  fireEvent.change(screen.getByTestId(testid), { target: { value } });

describe("<RangeControl />", () => {
  it("offers every mode and every granularity, and starts on the default", () => {
    renderAt();
    for (const id of ["this-month", "this-quarter", "this-year", "last-year", "last-12-months", "all", "custom"]) {
      expect(screen.getByTestId(`range-${id}`)).toBeInTheDocument();
    }
    for (const id of ["auto", "day", "week", "month", "quarter", "year"]) {
      expect(screen.getByTestId(`granularity-${id}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("range-this-year")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("granularity-auto")).toHaveAttribute("aria-pressed", "true");
    // Nothing in the URL: the default is spelled by the absence of a param.
    expect(screen.getByTestId("url")).toHaveTextContent("/insights/overview");
  });

  it("writes the preset to the URL and resolves the same window", async () => {
    const h = renderAt();
    await userEvent.setup().click(screen.getByTestId("range-this-quarter"));
    expect(h.state().mode).toBe("this-quarter");
    expect(h.url()).toContain("range=this-quarter");
    // The window the control reports is the one the page will query with.
    const { start, end } = resolvePreset("this-quarter");
    expect(h.state().start).toBe(start);
    expect(h.state().end).toBe(end);
  });

  it("drops the param again when the default preset is picked", async () => {
    const h = renderAt("?range=last-year");
    expect(h.state().mode).toBe("last-year");
    await userEvent.setup().click(screen.getByTestId("range-this-year"));
    // Back to a bare URL, so the default preset and its explicit spelling share
    // one cache entry rather than being two keys for one window.
    expect(h.url()).toBe("/insights/overview");
    expect(h.state().mode).toBe("this-year");
  });

  it("carries the granularity, and drops it at the default", async () => {
    const h = renderAt();
    await userEvent.setup().click(screen.getByTestId("granularity-quarter"));
    expect(h.url()).toContain("granularity=quarter");
    expect(h.state().granularity).toBe("quarter");
    await userEvent.setup().click(screen.getByTestId("granularity-auto"));
    expect(h.url()).not.toContain("granularity");
    expect(h.state().granularity).toBe("auto");
  });

  it("seeds the custom inputs with the window already on screen", async () => {
    const h = renderAt("?range=last-year");
    const before = h.state();
    await userEvent.setup().click(screen.getByTestId("range-custom"));
    // Not two empty boxes: choosing Custom should show the reader where they
    // were, so the first edit is a nudge rather than a retype.
    expect(h.state().mode).toBe("custom");
    expect(screen.getByTestId("range-start")).toHaveValue(before.start);
    expect(screen.getByTestId("range-end")).toHaveValue(before.end);
    expect(h.url()).toContain("range=custom");
    expect(h.url()).not.toContain("last-year");
  });

  it("takes a custom window verbatim", async () => {
    const h = renderAt();
    await userEvent.setup().click(screen.getByTestId("range-custom"));
    setDate("range-start", "2024-02-01");
    setDate("range-end", "2024-03-09");
    expect(h.url()).toContain("range=custom");
    expect(h.url()).toContain("start=2024-02-01");
    expect(h.url()).toContain("end=2024-03-09");
    expect(h.state()).toMatchObject({ mode: "custom", start: "2024-02-01", end: "2024-03-09" });
  });

  it("says so, in place, when the window is inverted", async () => {
    const h = renderAt();
    await userEvent.setup().click(screen.getByTestId("range-custom"));
    expect(screen.queryByTestId("range-invalid")).not.toBeInTheDocument();
    setDate("range-start", "2026-06-01");
    setDate("range-end", "2026-01-01");
    // Announced without moving focus (WCAG 4.1.3), the same as every other
    // validation message in the app — the reader is mid-edit in a date field.
    expect(screen.getByTestId("range-invalid")).toHaveAttribute("role", "alert");
    // And the state still says what the reader typed: the control reports the
    // problem rather than silently repairing it to something they did not ask for.
    expect(h.state()).toMatchObject({ start: "2026-06-01", end: "2026-01-01" });
    setDate("range-end", "2026-12-31");
    expect(screen.queryByTestId("range-invalid")).not.toBeInTheDocument();
  });

  it("ignores a half-typed date instead of falling back to a preset", async () => {
    const h = renderAt();
    await userEvent.setup().click(screen.getByTestId("range-custom"));
    setDate("range-start", "2024-05-05");
    // An emptied date input fires with `""`. Acting on it would drop the reader
    // out of Custom mid-edit; the last complete window stays on screen instead.
    setDate("range-start", "");
    expect(h.state().mode).toBe("custom");
    expect(h.state().start).toBe("2024-05-05");
  });

  it("recovers from a link it cannot use", () => {
    // Both halves go through the same reader, so a nonsense cut and a nonsense
    // window fail the same way: the default report, not an error page.
    renderAt("?range=fortnight&granularity=fortnightly");
    expect(screen.getByTestId("range-this-year")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("granularity-auto")).toHaveAttribute("aria-pressed", "true");
  });

  it("gives every element it renders its own testid", async () => {
    // Two elements answering to one testid is a strict-mode violation in every
    // spec that reaches for it — and the Custom chip and its two date inputs are
    // exactly the pair that would collide if the names drifted back together.
    renderAt();
    await userEvent.setup().click(screen.getByTestId("range-custom"));
    const ids = [...document.querySelectorAll("[data-testid]")]
      .map((el) => el.getAttribute("data-testid") ?? "")
      .filter((id) => id.startsWith("range-") || id.startsWith("granularity-"));
    expect(new Set(ids).size, `duplicate testids: ${ids.join(", ")}`).toBe(ids.length);
  });

  it("leaves the window open when All time is picked", async () => {
    const h = renderAt();
    await userEvent.setup().click(screen.getByTestId("range-all"));
    // `null` is the whole point: no client can compute where the data begins, so
    // the request omits `start` and the server answers with the day it used.
    expect(h.state().start).toBeNull();
    expect(h.url()).toContain("range=all");
    expect(h.url()).not.toContain("start=");
  });
});
