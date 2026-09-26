// Placeholder for the Insights → Allocations tab (session 09, task B4:
// `feat/insights-allocations`, stacked on this branch). The real content
// moves here from `AllocationBreakdown.tsx` / `InvestmentsView` — group-by
// switch, bars, the unpriced/FX counts — and gains bank cash and a tap-through
// detail sheet (ADR-0054, drafted alongside that work, not this one). This
// tab exists now so the Insights shell (routing, the tab list, ScrollTabs) is
// there for that branch to build on without also blocking it on the shell.
export default function Allocations() {
  return (
    <div className="rounded-card bg-surface-raised p-6 text-center" data-testid="insights-allocations-placeholder">
      <p className="text-sm text-fg-muted">
        Allocations is moving here from Accounts → Investments. Not wired up yet.
      </p>
    </div>
  );
}
