// The investments vertical's read surface (ADR-0011/0032/0034): the consolidated
// allocation and the valued portfolio, plus the recorded positions the
// portfolio's own response does not carry.
//
// Kept out of `api/hooks.ts` (shared, and pinned by other workstreams) while
// following the conventions in `api/rules.ts`.
//
// **Why two calls for the holdings list.** `/investments/portfolio` values a
// position; `/investments/holdings` says where that position's quantity came
// from (ADR-0034's `quantity_source` / `manual_quantity`). Neither response has
// the other's fields, and the household's own entry must not be silently
// dropped, so the view joins them — see `positionKey`. `holding_id` is nullable
// on both sides for a position that exists only as recorded trades, which is why
// the join is on `(account_id, security_id)`: that pair is what a position is.
//
// **The valuation date is not sent.** The API values as of *its own* today and
// says so in `as_of`, and that choice is deliberate on the server (`_today` in
// app/api/investments.py: UTC, so the same request does not value differently on
// two machines). A client-supplied local day would disagree with it for part of
// every day for a viewer west of UTC — and the staleness figures are relative to
// the date actually used, so the date the response names is the one to render.

import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Allocation, AllocationGroup, Holding, Portfolio, UUID } from "@/api/types";

/**
 * The cross-account allocation, grouped by `groupBy`.
 *
 * `group_by` is part of the key: each grouping is its own response, and switching
 * back to one already fetched must not refetch it.
 */
export function useAllocation(groupBy: AllocationGroup) {
  return useQuery({
    queryKey: ["allocation", groupBy],
    queryFn: () =>
      api.get<Allocation>(`/investments/allocation?group_by=${encodeURIComponent(groupBy)}`),
  });
}

/** Every investment account, valued, with the positions that could not be valued
 *  listed rather than counted as zero. */
export function usePortfolio() {
  return useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api.get<Portfolio>("/investments/portfolio"),
  });
}

/** Every position as recorded, with its effective quantity and the source of it
 *  (ADR-0034). Household-wide; there is no owner scope on this endpoint. */
export function useHoldings() {
  return useQuery({ queryKey: ["holdings"], queryFn: () => api.get<Holding[]>("/investments/holdings") });
}

/** The identity of a position, and therefore the key the two endpoints join on.
 *  A position is one per (account, security); a security held in three accounts
 *  is three positions. */
export function positionKey(accountId: UUID, securityId: UUID): string {
  return `${accountId}:${securityId}`;
}

/** `/investments/holdings` indexed by what identifies a position, for the
 *  lookup the holdings list does per valued row. Empty while loading, so a row
 *  renders without its ADR-0034 line rather than refusing to render. */
export function indexHoldings(holdings: Holding[] | undefined): Map<string, Holding> {
  const index = new Map<string, Holding>();
  for (const h of holdings ?? []) index.set(positionKey(h.account_id, h.security_id), h);
  return index;
}
