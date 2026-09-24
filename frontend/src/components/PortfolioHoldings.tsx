// The holdings list, valued: every investment account, its market value, and
// every position inside it with its quantity, price, value and price age.
//
// Two things here are load-bearing and neither is cosmetic:
//
// **A position that cannot be valued is not a position worth nothing.** ADR-0032
// §5 returns `reason` (`no_price` / `no_rate`) and a null `value_base` precisely
// so that the two can be told apart on screen. So a zero is *never* rendered for
// one: the row names the reason in words, and the total it is missing from says
// so beside the total. `$0.00` appears only where the database really holds a
// zero — a written-off position with a price of zero, which is a different fact.
//
// **ADR-0034's override is shown, not silently applied.** When recorded trades
// exist they are the position, and a hand-entered quantity is overridden by them.
// The household can still see its own number in the database, so the row says
// what was entered and that history is what counts — silently disagreeing with
// the household's own entry is the failure that ADR exists to prevent. That data
// is not on the valuation response, so it comes from `/investments/holdings` and
// is joined per position (see `api/investments.ts`).

import { useHoldings, usePortfolio, indexHoldings, positionKey } from "@/api/investments";
import type { AccountValuation, Holding, HoldingValue, Portfolio } from "@/api/types";
import { Day } from "@/components/datetime";
import { Button } from "@/components/form";
import { QueryError, SkeletonRows } from "@/components/QueryStates";
import { formatMoney } from "@/lib/format";
import {
  STALE_DAYS,
  excludedSentence,
  formatPrice,
  isCash,
  isZeroDecimal,
  sameQuantity,
  securityTypeLabel,
  trimDecimal,
} from "@/lib/investments";

export default function PortfolioHoldings() {
  const portfolio = usePortfolio();
  const holdings = useHoldings();
  const recorded = indexHoldings(holdings.data);

  const excluded = portfolio.data
    ? excludedSentence(
        portfolio.data.accounts.reduce((n, a) => n + a.unpriced, 0),
        portfolio.data.accounts.reduce((n, a) => n + a.no_rate, 0),
      )
    : null;

  return (
    <section className="space-y-3" data-testid="portfolio">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Holdings</h2>
        {portfolio.data && (
          <p className="text-xs text-fg-muted" data-testid="portfolio-as-of">
            as of <Day value={portfolio.data.as_of} />
          </p>
        )}
      </div>

      {portfolio.isError ? (
        <QueryError
          what="your holdings"
          error={portfolio.error}
          onRetry={() => portfolio.refetch()}
          testid="portfolio-error"
        />
      ) : portfolio.isPending || !portfolio.data ? (
        <SkeletonRows what="your holdings" rows={4} testid="portfolio-loading" />
      ) : (
        <>
          {/* No total card when there is nothing to total: `$0.00` above "No
              investment accounts yet." states a figure about a household that has
              none, and the allocation card beside it does not (it shows its empty
              state alone). Two cards agreeing about what an empty portfolio looks
              like is worth more than the reassurance of a zero — and a zero here
              would be read as "your investments are worth nothing", which is a
              different and alarming claim. */}
          {portfolio.data.accounts.length > 0 && (
            <div className="rounded-card bg-surface-raised p-4">
              <p className="text-sm text-fg-muted">Value · {portfolio.data.base_currency}</p>
              <p className="text-2xl font-semibold" data-testid="portfolio-total">
                {formatMoney(portfolio.data.total_base, portfolio.data.base_currency)}
              </p>
              {excluded && (
                <p className="mt-1 text-xs text-warning" role="status" data-testid="portfolio-excluded">
                  {excluded}
                </p>
              )}
            </div>
          )}

          {/* The ADR-0034 line is the *only* thing `/investments/holdings` adds,
              and it comes from a second call. A failure there is not a failure to
              show the portfolio — every value above is still true — but it does
              mean every row below is missing the override it may have had, and
              **nothing appearing is indistinguishable from nothing to appear**. A
              household with no override to show and a request that never arrived
              render identically unless one of them says so (§4.11's rule for a
              supplementary read). */}
          {portfolio.data.accounts.length === 0 ? (
            <p
              className="rounded-card bg-surface-raised px-4 py-8 text-center text-sm text-fg-muted"
              data-testid="portfolio-empty"
            >
              No investment accounts yet.
              {/* An "Add a holding" entry point belongs here — out of scope for
                  this workstream, which is the read side of UINV. */}
            </p>
          ) : (
            <>
              {holdings.isError && (
                <div role="status" data-testid="holdings-error">
                  <p className="text-xs text-warning">
                    Couldn’t load your recorded positions, so no row below can say whether a quantity
                    you entered by hand was overridden by trades.
                  </p>
                  {/* The retry is the same escape §4.11 gives every other failed
                      read. Not the `QueryError` card: that one replaces the
                      section and would claim the portfolio itself failed. */}
                  <Button
                    variant="ghost"
                    className="-ml-2 mt-1"
                    onClick={() => holdings.refetch()}
                    data-testid="holdings-error-retry"
                  >
                    Try again
                  </Button>
                </div>
              )}
              {portfolio.data.accounts.map((a) => (
                <AccountCard key={a.account_id} account={a} portfolio={portfolio.data} recorded={recorded} />
              ))}
            </>
          )}
        </>
      )}
    </section>
  );
}

function AccountCard({
  account,
  portfolio,
  recorded,
}: {
  account: AccountValuation;
  portfolio: Portfolio;
  recorded: Map<string, Holding>;
}) {
  const ccy = portfolio.base_currency;
  const excluded = excludedSentence(account.unpriced, account.no_rate);
  // ADR-0021's plug: for a `stated` account the provider's balance is the
  // authoritative number, and the part of it these holdings do not account for
  // is real money in the account rather than a rounding error.
  // No positions at all is its own case, and the common one for a synced
  // account: SimpleFIN reports a balance and nothing it holds. "Unaccounted cash
  // $95,838" read as money gone astray; the truth is simpler, and said plainly.
  const noPositions = account.holdings.length === 0;
  const unaccounted = !noPositions && !isZeroDecimal(account.unaccounted_cash_base);
  // **The headline is what the total counts this account at.** For a `derived`
  // account that is Σ(holdings) and equals `market_value_base`. For a `stated`
  // one the authoritative number is the provider's balance — and `market_value_base`
  // is still only Σ(holdings), so rendering it here would print a figure that is
  // neither the account's balance nor the account's line in the total below it.
  // `stated_balance_base` is the stated balance in base, which is exactly the
  // number `value_portfolio` adds.
  const contribution = account.stated_balance_base ?? account.market_value_base;
  // A `stated` account whose balance has no rate: no plug was computable, so the
  // account contributed nothing to the total. That is the one case where the
  // headline cannot be a base figure at all, and it is said rather than shown as
  // `$0.00` — a missing rate must never render as a number (§6.4, ADR-0017).
  const statedUnconvertible = account.balance_source === "stated" && account.stated_balance_base === null;

  return (
    <section
      className="rounded-card bg-surface-raised"
      data-testid={`account-valuation-${account.account_id}`}
    >
      <header className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h3 className="font-medium">{account.name}</h3>
          <p className="text-xs text-fg-muted">
            {account.currency} ·{" "}
            {account.balance_source === "stated"
              ? "balance from the account"
              : noPositions
                ? "balance as entered"
                : "balance from its holdings"}
            {/* §6.4: a converted figure names the currency it was converted
                from, so a cross-currency account is not read as a base-currency
                one. `balance_account` — not `market_value_account` — because it
                is the account's own balance either way: a `stated` account has
                one whether or not its holdings do, and Σ(holdings) is the wrong
                number to put beside the words "balance from the account". */}
            {account.currency !== ccy && (
              <span className="ml-1" data-testid={`account-native-${account.account_id}`}>
                · {formatMoney(account.balance_account, account.currency)} {account.currency}
              </span>
            )}
          </p>
        </div>
        <p className="ml-auto shrink-0 font-semibold" data-testid={`account-value-${account.account_id}`}>
          {statedUnconvertible ? (
            <span className="text-warning font-medium">No {ccy} rate</span>
          ) : (
            formatMoney(contribution, ccy)
          )}
        </p>
      </header>

      <div role="status" aria-atomic="true">
        {statedUnconvertible && (
          <p className="px-4 pt-2 text-xs text-warning" data-testid={`account-no-rate-${account.account_id}`}>
            This account's balance is {formatMoney(account.balance_account, account.currency)}{" "}
            {account.currency} and there is no rate to convert it, so it is counted at nothing in the
            total above — not at zero.
          </p>
        )}
        {excluded && (
          <p className="px-4 pt-2 text-xs text-warning" data-testid={`account-excluded-${account.account_id}`}>
            {excluded}
          </p>
        )}
        {unaccounted && (
          <p className="px-4 pt-2 text-xs text-fg-muted" data-testid={`account-unaccounted-${account.account_id}`}>
            Unaccounted cash {formatMoney(account.unaccounted_cash_base, ccy)} — the stated balance
            less what these positions account for.
          </p>
        )}
        {account.max_stale_days !== null && (
          <p
            className={`px-4 pt-2 text-xs ${account.max_stale_days > STALE_DAYS ? "text-warning" : "text-fg-muted"}`}
            data-testid={`account-stale-${account.account_id}`}
          >
            Oldest price used: {account.max_stale_days}{" "}
            {account.max_stale_days === 1 ? "day" : "days"} old
            {account.oldest_price_date && (
              <>
                {" ("}
                <Day value={account.oldest_price_date} />
                {")"}
              </>
            )}
            .
          </p>
        )}
      </div>

      <ul className="divide-y divide-border" data-testid={`account-holdings-${account.account_id}`}>
        {account.holdings.map((h) => (
          <HoldingRow
            key={h.holding_id ?? positionKey(h.account_id, h.security_id)}
            holding={h}
            baseCurrency={ccy}
            asOf={portfolio.as_of}
            recorded={recorded.get(positionKey(h.account_id, h.security_id))}
          />
        ))}
        {account.holdings.length === 0 && (
          <li className="px-4 py-4 text-sm text-fg-muted" data-testid={`account-no-positions-${account.account_id}`}>
            {account.balance_source === "stated"
              ? "The bank reports this account’s balance, not what it holds. Add its positions to see the breakdown."
              : "No positions recorded yet — the balance above is the one entered for the account."}
          </li>
        )}
      </ul>
    </section>
  );
}

function HoldingRow({
  holding: h,
  baseCurrency,
  asOf,
  recorded,
}: {
  holding: HoldingValue;
  baseCurrency: string;
  asOf: string;
  recorded: Holding | undefined;
}) {
  const unpriced = h.reason !== null;
  const cash = isCash(h.security_type);
  // The row's identity is the *position*, not the security: one security held in
  // two accounts is two rows, and a testid keyed on the security alone would name
  // both of them. `positionKey` is the same identity the two endpoints join on,
  // so there is one definition of "which position is this" in the app.
  const pos = positionKey(h.account_id, h.security_id);

  return (
    <li
      className="px-4 py-3"
      // The machine-readable half of the row: a test (and a reader of the DOM)
      // can tell "no price" from "$0.00" without parsing the copy.
      data-reason={h.reason ?? "valued"}
      data-testid={`holding-${pos}`}
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">
            {h.name}
            {h.ticker && <span className="ml-2 text-xs text-fg-muted">{h.ticker}</span>}
          </p>
          {/* Cash is a `security_type` (ADR-0033 §4), not an instrument whose
              quote is missing — so it is named as what it is. */}
          <p className="text-xs text-fg-muted">{securityTypeLabel(h.security_type)}</p>
        </div>

        {/* Never a zero, and never an em dash: the row says which of the two
            refusals it is, in words. Words and not colour, because colour alone
            is not a signal (§7.8) and this is the distinction the view exists
            for. */}
        <p
          className={`shrink-0 text-right ${unpriced ? "font-medium text-warning" : "font-medium"}`}
          data-testid={`holding-value-${pos}`}
        >
          {/* Keyed on the value, falling back to the reason: a null value with no
              reason at all still renders as a refusal rather than as `$0.00`,
              because that is the one rendering this row must never produce. */}
          {h.value_base !== null
            ? formatMoney(h.value_base, baseCurrency)
            : h.reason === "no_rate"
              ? "No rate"
              : "No price"}
        </p>
      </div>

      <p className="mt-0.5 text-xs text-fg-muted" data-testid={`holding-detail-${pos}`}>
        {/* The position in the notation every brokerage statement uses: what is
            held, at what price. Quantities arrive as 8 dp decimal strings, so
            they are trimmed rather than floated (ADR-0005).

            Cash is the exception, and it is not a special case so much as the
            same sentence read all the way through: a cash position's quantity
            *is* its amount, so 1,200 units of USD is $1,200.00 and reading it as
            "1,200 at $1.00" is strictly worse. Only the price's own currency is
            used to say so — falling back to the account's currency would label
            EUR cash as dollars on an account in USD. */}
        {cash && h.price_currency !== null ? (
          <span>{formatMoney(h.quantity, h.price_currency)}</span>
        ) : (
          <span>
            {trimDecimal(h.quantity)}
            {h.price !== null && h.price_currency !== null && (
              <> @ {formatPrice(h.price, h.price_currency)}</>
            )}
          </span>
        )}
        {/* The age is stated for every priced position, at every value: the
            valuation is only as current as the oldest price in it, and a total
            frozen at an old quote must not read as a flat market. */}
        {h.price_date !== null && h.stale_days !== null && (
          <span className={h.stale_days > STALE_DAYS ? "text-warning" : undefined}>
            {" "}
            · priced <Day value={h.price_date} style="compact" /> ({h.stale_days}{" "}
            {h.stale_days === 1 ? "day" : "days"} old)
          </span>
        )}
      </p>

      {h.reason === "no_price" && (
        <p className="mt-1 text-xs text-warning" data-testid={`holding-reason-${pos}`}>
          No price on or before <Day value={asOf} /> — left out of every total, not counted as zero.
        </p>
      )}

      {h.reason === "no_rate" && (
        <p className="mt-1 text-xs text-warning" data-testid={`holding-reason-${pos}`}>
          {/* `no_rate` is not "we know nothing": the position is priced in its own
              currency, and only the conversion is missing — so the part that *is*
              known is shown, and the part that is not is named. */}
          {h.value_native !== null && h.price_currency !== null
            ? `${formatMoney(h.value_native, h.price_currency)} ${h.price_currency} cannot be converted to ${baseCurrency}`
            : `No ${baseCurrency} rate`}{" "}
          on or before <Day value={asOf} /> — left out of every total, not counted as zero.
        </p>
      )}

      <ManualOverride pos={pos} recorded={recorded} />
    </li>
  );
}

/**
 * ADR-0034's disagreement, when there is one to show.
 *
 * Rendered only when all three facts hold: history is what the position is, a
 * human typed something, and the two differ. A position with no row behind it
 * (`manual_quantity` null) has nothing to report, and one where the typed number
 * agrees (a quantity entered before the first trade, or re-entered to match) has
 * nothing to reconcile.
 *
 * The null check is explicit rather than folded into `sameQuantity`, because
 * `sameQuantity` answers "do these two numbers agree" and its answer for a
 * missing one is `false` — correct for a comparison, and the wrong input to a
 * guard that decides whether to *print a number*. Reading it as "they disagree"
 * is how this line came to announce `You entered 0` to a household that had
 * entered nothing, on exactly the position ADR-0034 exists for.
 */
function ManualOverride({ pos, recorded }: { pos: string; recorded: Holding | undefined }) {
  if (!recorded) return null;
  if (recorded.quantity_source !== "history") return null;
  if (recorded.manual_quantity === null) return null;
  if (sameQuantity(recorded.quantity, recorded.manual_quantity)) return null;

  return (
    <p className="mt-1 text-xs text-fg-muted" data-testid={`holding-manual-${pos}`}>
      You entered {trimDecimal(recorded.manual_quantity)} — recorded trades give{" "}
      {trimDecimal(recorded.quantity)}, and history is what counts here.
    </p>
  );
}
