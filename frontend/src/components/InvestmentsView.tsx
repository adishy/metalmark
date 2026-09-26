// The investments view of the Accounts page: the valued holdings list, plus a
// link to where the allocation moved. docs/ARCHITECTURE.md §4 asks for the
// consolidated view — "a single page that aggregates every holding across all
// accounts" — and it is a *view of* an existing destination rather than a
// destination of its own.
//
// That is a design decision, not a shortcut. DESIGN.md §4.13 caps the phone tab
// bar at five items and says a new destination with no room there belongs inside
// an existing one rather than becoming a squeezed sixth tab; the Accounts
// destination already owns the household's balances, and a portfolio is the same
// subject seen at a different depth. `AppShell.tsx`'s `NAV` is deliberately
// unchanged.
//
// The allocation card itself moved to Insights → Allocations (session 09, task
// B4): it grew a "include bank cash" toggle and a tap-through detail sheet, both
// of which are about the *household's money*, not about this one page's holdings
// list — Insights is where the household reads and compares things, and this
// page's job is a specific account's own numbers. What is left here is one link
// row, so the trail from "the portfolio" to "the whole picture" is not lost.
//
// **Two of the spec's four row fields are not here, and that is a known gap, not
// an oversight.** §4 names the summed row as "(quantity, total market value, cost
// basis, gain/loss)". Market value is delivered, as is the % allocation and the
// four group-by toggles. Quantity, cost basis and gain/loss are not:
//
//   - `/investments/allocation` has no field for them, so they would have to be
//     re-derived by joining `/investments/holdings` — and that join is only
//     coherent for `group_by=security`, since a quantity summed across *accounts*
//     mixes different securities and means nothing.
//   - `cost_basis` there is in the security's own quote currency, and the view's
//     figures are in the base. Converting it needs a rate **at each acquisition**,
//     not at the valuation date — that is an accounting policy (which ADR-0032
//     does not yet define), and inventing one quietly is how a wrong gain/loss
//     reaches the screen. Showing the native figure instead is defensible but only
//     under `group_by=security`, which makes it a design call rather than a patch.
//
// Recorded in docs/PLAN-v0.9.md (§"What this plan deliberately does not do") so
// it is not lost. The alternative — a "gain/loss" whose currency policy nobody
// wrote down — is worse than its absence.
//
// **Read side only.** Entry and edit forms for securities, prices, holdings and
// investment transactions are the rest of the UINV workstream and are not built
// here, so there is no "Add" button anywhere below — a control that opens
// nothing is worse than its absence, and the empty states say where one would
// go.

import { Link } from "react-router-dom";
import { ChevronRightIcon } from "@/components/icons";
import PortfolioHoldings from "@/components/PortfolioHoldings";

export default function InvestmentsView({ ownerName }: { ownerName?: string | null }) {
  return (
    // A single column, and it is one on purpose. This view held a two-up grid
    // because the allocation card lived here and the holdings list needed
    // something beside it; the allocation moved to Insights (ADR-0054) and what
    // is left in the first cell is one 44 px link, which made the grid a
    // liability rather than a layout: the row was as tall as the holdings list,
    // so the grid's whole second column was empty space with a link at the top
    // of it. There is no second thing of substance to put beside the holdings,
    // and inventing one to fill a column is not a layout — so the pointer is a
    // full-width row above them instead, and the page is one column of content
    // that is the width it says it is.
    //
    // The holdings themselves still grid at `lg:` — one card per account, two
    // up — which is §9.3's card grid doing its job where there is in fact more
    // than one card to lay out.
    <div className="space-y-6" data-testid="investments-view">
      {/* The owner filter is a Balances control — it scopes that view's list and
          its net-worth header — and the portfolio endpoints have no owner scope
          (`api/investments.ts`). Switching views while filtered would otherwise
          swap household-wide totals in under an active filter with nothing said,
          which reads as the filter having silently stopped working. */}
      {ownerName && (
        <p className="text-sm text-fg-muted" role="status" data-testid="investments-owner-note">
          Every owner — the portfolio is household-wide, so {ownerName}’s filter does not apply here.
        </p>
      )}
      <Link
        to="/insights/allocations"
        className="flex min-h-11 items-center justify-between gap-3 rounded-card bg-surface-raised p-4 text-sm hover:bg-surface-inset"
        data-testid="allocation-moved-link"
      >
        See allocation in Insights
        <ChevronRightIcon aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
      </Link>
      <PortfolioHoldings />
    </div>
  );
}
