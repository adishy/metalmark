// The investments view of the Accounts page: the consolidated allocation and the
// valued holdings list, one under the other. docs/ARCHITECTURE.md §4 asks for the
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

import AllocationBreakdown from "@/components/AllocationBreakdown";
import PortfolioHoldings from "@/components/PortfolioHoldings";

export default function InvestmentsView({ ownerName }: { ownerName?: string | null }) {
  return (
    // §9.3's card grid, and these two are the cards: the allocation a reader
    // asks about first, and the holdings that explain it. Side by side at `lg:`
    // the breakdown sits next to the thing it is a breakdown *of*, which is the
    // one arrangement where a donut and a table argue with each other usefully.
    // The owner note spans both because it is about the view, not about either
    // card.
    <div
      className="space-y-6 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0"
      data-testid="investments-view"
    >
      {/* The owner filter is a Balances control — it scopes that view's list and
          its net-worth header — and the portfolio endpoints have no owner scope
          (`api/investments.ts`). Switching views while filtered would otherwise
          swap household-wide totals in under an active filter with nothing said,
          which reads as the filter having silently stopped working. */}
      {ownerName && (
        <p
          className="text-sm text-fg-muted lg:col-span-2"
          role="status"
          data-testid="investments-owner-note"
        >
          Every owner — the portfolio is household-wide, so {ownerName}’s filter does not apply here.
        </p>
      )}
      <AllocationBreakdown />
      <PortfolioHoldings />
    </div>
  );
}
