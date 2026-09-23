// Mirrors contracts/openapi.yaml (kept in sync by hand for v1; a generated
// client can replace this later). Money values arrive as decimal strings.

export type UUID = string;
export type Money = string;

export interface Me {
  user: { id: UUID; email: string; display_name: string; is_admin: boolean };
  household_id: UUID;
  household_name: string;
  base_currency: string;
  role: string;
  csrf_token: string;
}

export type AccountType = "depository" | "credit" | "investment" | "loan" | "other";

export type OwnerKind = "person" | "shared";

/** A household data row, not a user. Exactly one kind="shared" owner exists per
 * household ("Shared") and is created with it; it cannot be deleted. */
export interface Owner {
  id: UUID;
  name: string;
  kind: OwnerKind;
  sort: number;
}

export interface Account {
  id: UUID;
  name: string;
  type: AccountType;
  currency: string;
  subtype: string | null;
  institution: string | null;
  current_balance: Money;
  balance_date: string | null;
  is_asset: boolean;
  /** The date of the last balance of a synced account the bank has stopped
   *  reporting — still carried forward on the net-worth line. */
  stale_since?: string | null;
  /** Always set — an account is the bottom of the ownership chain. */
  owner_id: UUID;
  is_manual: boolean;
  is_hidden: boolean;
}

export interface NetWorth {
  base_currency: string;
  assets: Money;
  liabilities: Money;
  net_worth: Money;
  unconverted_currencies: string[];
}

export interface CategoryGroup {
  id: UUID;
  name: string;
  type: "income" | "expense" | "transfer";
  sort: number;
}

export interface Category {
  id: UUID;
  group_id: UUID;
  name: string;
  icon: string | null;
  color: string | null;
  sort: number;
}

export interface Tag {
  id: UUID;
  name: string;
  color: string | null;
}

export interface Split {
  id: UUID;
  amount: Money;
  base_amount: Money | null;
  category_id: UUID | null;
  /** null = inherit from the transaction. */
  owner_id: UUID | null;
  /** Resolved server-side: split -> transaction -> account -> Shared. */
  effective_owner_id: UUID;
  notes: string | null;
}

export interface Transaction {
  id: UUID;
  account_id: UUID;
  amount: Money;
  currency: string;
  base_amount: Money | null;
  fx_rate_date: string | null;
  transacted_at: string;
  posted_at: string | null;
  description: string | null;
  merchant: string | null;
  category_id: UUID | null;
  /** null = inherit from the account. */
  owner_id: UUID | null;
  /** Resolved server-side: transaction -> account -> Shared. */
  effective_owner_id: UUID;
  is_pending: boolean;
  review_status: "needs_review" | "reviewed" | "ignored";
  is_hidden: boolean;
  is_split_parent: boolean;
  transfer_group_id: UUID | null;
  field_sources: Record<string, string>;
  notes: string | null;
  source: string;
  tag_ids: UUID[];
  splits: Split[];
}

export interface TransactionPage {
  items: Transaction[];
  next_cursor: string | null;
}

export type MissingReason = "not_started" | "no_balance" | "no_rate" | "no_price";

export interface MissingAccount {
  account_id: UUID;
  name: string;
  reason: MissingReason;
}

export interface NetWorthSeries extends ReportWindow {
  base_currency: string;
  /**
   * Sets the **points and nothing else**. Every term of the identity below is
   * scoped to the whole window, so switching the chart from months to quarters
   * redraws it without moving a single number in the reconciliation.
   */
  granularity: Granularity;
  /** `missing`: the accounts a point does not fully count, and why (ADR-0045). */
  points: { date: string; net_worth: Money; missing: MissingAccount[] }[];
  delta_net_worth: Money;
  net_cash_flow: Money;
  currency_revaluation: Money;
  /**
   * The change in position values less the buys that funded them (ADR-0032 §3).
   * A term of the identity below, and never `delta` minus the others — the
   * whole point is that it is computed independently and can disagree.
   */
  market_appreciation: Money;
  /**
   * `delta − cash flow − revaluation − appreciation`: the only term that is a
   * subtraction, and therefore the one that says something when it is not zero.
   *
   * A non-zero value here is not automatically a bug — an account whose balance
   * is `stated` has history we cannot attribute (ADR-0032 §6) — which is why it
   * is rendered as a term of its own rather than hidden.
   */
  unexplained: Money;
  /**
   * Which accounts the residual came from — the name behind the number, largest
   * first, and only the material ones (the service drops anything under 1% of the
   * residual). A residual with no name is not a finding: this is what turns
   * "unexplained $38,850.60" into an account a person can go and look at.
   *
   * The list is a *floor*, not the whole story — the parts that were too small to
   * name are still inside `unexplained` — so it is rendered as "of which", never
   * as a decomposition that adds up.
   */
  unexplained_by_account: { account_id: string; name: string; amount: Money }[];
  /**
   * Reasons a term is missing part of its data: no FX rate for a trade, a
   * position with no price. **Rendered whenever non-empty**: a term computed
   * from partial data is a number that is wrong in a way nobody can see, which
   * is the failure ADR-0032 §5 refuses to render as a zero.
   */
  warnings: string[];
  /** Ownership only ever moves whole accounts, never individual postings. */
  attribution: "account";
}

/**
 * A window a report answered for, echoed by the server on every report.
 *
 * Carried rather than remembered: a chart labels its own axis from the data it
 * drew, so the picture and the payload cannot disagree about what is on screen.
 */
export interface ReportWindow {
  start: string;
  end: string;
}

/**
 * How finely a window is cut. `auto` is a **request** and never a response —
 * the server resolves it and echoes the period it picked, so a chart never has
 * to resolve the span itself and risk disagreeing with the bars it was given.
 */
export type Granularity = "day" | "week" | "month" | "quarter" | "year";
export type GranularityParam = "auto" | Granularity;

export interface CashFlowPoint {
  /**
   * The bucket's first day **inside the window** — never the period it belongs
   * to, which for a window opening mid-month would be a day outside the range
   * this series reports.
   */
  date: string;
  income: Money;
  expense: Money;
  net: Money;
}

export interface CashFlowSeries extends ReportWindow {
  base_currency: string;
  granularity: Granularity;
  points: CashFlowPoint[];
  /** Every entry stands on its own owner — the mirror of net worth's "account". */
  attribution: "row";
}

/**
 * One node of the cash-flow graph.
 *
 * `total` is a **magnitude** — always positive — because the list the row is in
 * carries the direction. See `CashFlowSankey`.
 */
export interface CashFlowSankeyRow {
  /**
   * The row's *identity*, which is neither its label nor its `category_id`.
   *
   * Two categories may share a name and a household may name one
   * "Uncategorized", so a graph keyed on what a reader sees would merge unrelated
   * money. Node ids are built from this; the rows that are not categories at all
   * (`"uncategorized"`, `"investment:dividend"`, `"investment:fee"`) get keys of
   * their own so they cannot collide with a real category either.
   */
  key: string;
  label: string;
  /** `null` for the rows above that are not a category. */
  category_id: UUID | null;
  total: Money;
}

/**
 * The window's cash flow as the two sides of one graph.
 *
 * **Rows, not nodes and links**: grouping is the arithmetic and the picture's
 * shape follows from it, so the client builds the graph. Sending a layout would
 * put a decision on the wire that no reader could check.
 *
 * Every figure except `net` is a magnitude, which is why `total_expense` is
 * positive where `CashFlowPoint.expense` is not: a Sankey encodes direction by
 * which side a node sits on, and a value negative on both sides is not a graph
 * anyone can draw. `total_income` and `total_expense` are the sums of the rows
 * above them, so the picture balances by construction.
 */
export interface CashFlowSankey extends ReportWindow {
  base_currency: string;
  income: CashFlowSankeyRow[];
  expense: CashFlowSankeyRow[];
  total_income: Money;
  total_expense: Money;
  /** `total_income - total_expense`, and the value the middle of the graph holds. */
  net: Money;
  /** Same row-level reading as cash flow — the two are the same money. */
  attribution: "row";
  /** A flow dropped for want of a rate is a missing branch of a whole picture. */
  warnings: string[];
}

/**
 * One bucket of the window's spending.
 *
 * `total` is a **magnitude**, unlike `CashFlowPoint.expense`: spending is
 * positive here, as it is on the graph.
 */
export interface CategorySpendRow {
  /**
   * The row's *identity*. Not the category — investment fees are spending with
   * no category, so they carry `category_id: null` exactly as unfiled rows do.
   * A list or a chart keying on the category id would collapse those into one
   * row, which is why this exists and why it is the same key the Sankey builds
   * its node ids from.
   */
  key: string;
  category_id: UUID | null;
  category_name: string;
  total: Money;
}

export interface SpendingReport extends ReportWindow {
  base_currency: string;
  rows: CategorySpendRow[];
  /** The sum of the rows, and `-expense` on the cash-flow series over the same window. */
  total: Money;
  /** Same row-level reading as cash flow. */
  attribution: "row";
  /** A dropped flow here is spending the reader paid and this report does not show. */
  warnings: string[];
}

export interface Member {
  user_id: UUID;
  display_name: string;
  email: string;
  role: string;
}

export interface Household {
  id: UUID;
  name: string;
  base_currency: string;
  timezone: string;
  role: string;
}

export interface FxRate {
  id: UUID;
  base_currency: string;
  quote_currency: string;
  rate_date: string;
  rate: Money;
  source: string;
}

export interface OwnerReassignment {
  reassigned_accounts: number;
  reassigned_transactions: number;
  reassigned_splits: number;
}

// ---- bank connections and sync ----
//
// No access URL appears in any of these, in any form, by construction: the
// credential is encrypted at rest and `ConnectionOut` has no field that could
// carry it. Adding one here would not make it exist.

/** What the *bank* is doing. See `Connection.is_enabled` for what the user did. */
export type ConnectionStatus = "ok" | "auth_error" | "error";

/** A queued or running job, for the panel's live table. */
export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled" | "expired";

/** A finished run's outcome. `partial` means the bridge complained about some
 *  accounts but the rest of the payload was ingested. */
export type RunStatus = "running" | "ok" | "partial" | "error" | "cancelled";

export interface Connection {
  id: UUID;
  provider: string;
  /** The institution, from the first successful fetch. Null until then: the
   *  claim endpoint refuses to invent one. */
  org_name: string | null;
  status: ConnectionStatus;
  last_synced_at: string | null;
  last_error: string | null;
  /** False means paused. Independent of `status` — a paused connection keeps
   *  its health, so the UI can say both "you turned this off" and "the bank
   *  revoked us" when both are true. */
  is_enabled: boolean;
  sync_interval_minutes: number;
  next_sync_at: string | null;
  created_at: string;
}

export interface SyncJob {
  id: UUID;
  connection_id: UUID;
  trigger: string;
  status: JobStatus;
  attempts: number;
  created_at: string;
  not_before: string | null;
  claimed_at: string | null;
  /** The reaper's clock: a job whose heartbeat stopped advancing is a stuck
   *  job, and the panel shows the same signal the server acts on. */
  heartbeat_at: string | null;
  cancel_requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface SyncRun {
  id: UUID;
  /** SET NULL on disconnect — `connection_label` is what keeps the history
   *  readable after the connection is gone. */
  connection_id: UUID | null;
  connection_label: string | null;
  trigger: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  http_ms: number | null;
  http_status: number | null;
  bytes_fetched: number | null;
  accounts_seen: number;
  accounts_created: number;
  accounts_remapped: number;
  txns_rekeyed: number;
  txns_inserted: number;
  txns_updated: number;
  txns_reconciled: number;
  pendings_expired: number;
  transfers_matched: number;
  rules_applied: number;
  error: string | null;
}

export interface SyncRunEvent {
  id: UUID;
  /** The ordering key. `ts` cannot order a run's events: Postgres `now()` is
   *  the transaction timestamp, so a whole ingest shares it exactly. */
  seq: number;
  ts: string;
  level: "debug" | "info" | "warning" | "error";
  event: string;
  /** Sanitized at write time (ADR-0016), so it is safe to render as-is. */
  detail: Record<string, unknown>;
}

/** One run and its log — the expanded row, in one request. */
export interface SyncRunDetail {
  run: SyncRun;
  events: SyncRunEvent[];
}

/** One notice the worker decided to send, as the browser shows it (ADR-0037).
 *
 *  A title and a body, composed server-side — deliberately not the raw trouble
 *  fields. The decision that a failure is worth telling somebody about is
 *  `should_notify`'s, in the worker, and a page allowed to build its own wording
 *  from raw fields is a page that can be persuaded to put an amount in it. So
 *  this is shown as it arrives and never re-composed here.
 *
 *  Named `SyncNotice` rather than `Notification` because the display path goes
 *  through the DOM's `Notification` API and the two would shadow each other in
 *  any module that does both. */
export interface SyncNotice {
  id: UUID;
  run_id: UUID;
  ts: string;
  /** What the browser passes as the notification's `tag`, so a repeat about one
   *  connection replaces the standing notification rather than stacking a column
   *  of them. Null once the connection is gone — the notice is history and the
   *  failure still happened. */
  connection_id: UUID | null;
  title: string;
  body: string;
}

/** The cadence floor, ceiling and default, so the interval control needs no
 *  hard-coded copy of constants a CHECK constraint enforces. */
export interface ConnectionDefaults {
  sync_interval_minutes: number;
  sync_interval_min_minutes: number;
  sync_interval_max_minutes: number;
}

// ---- request bodies ----

export interface SignupCreate {
  email: string;
  display_name: string;
  password: string;
  /** Ignored when the household already exists (later signups just join it). */
  household_name?: string;
}

export interface HouseholdUpdate {
  name?: string;
  timezone?: string;
}

export interface OwnerCreate {
  name: string;
  sort?: number;
}

export interface OwnerUpdate {
  name?: string;
  sort?: number;
}

export interface AccountCreate {
  name: string;
  type: AccountType;
  currency: string;
  subtype?: string | null;
  institution?: string | null;
  /** Signed: a card or loan's debt is negative (ADR-0043). Omit for no opening balance. */
  current_balance?: Money;
  balance_date?: string | null;
  /** Omit to let the server assign the Shared owner. */
  owner_id?: UUID;
}

export interface AccountUpdate {
  name?: string;
  /** Correctable: balances are signed, so a retype does not rewrite history (ADR-0043). */
  type?: AccountType;
  subtype?: string | null;
  institution?: string | null;
  current_balance?: Money | null;
  balance_date?: string | null;
  owner_id?: UUID;
  is_hidden?: boolean | null;
}

export interface TransactionCreate {
  account_id: UUID;
  amount: Money;
  transacted_at: string;
  posted_at?: string | null;
  description?: string | null;
  merchant?: string | null;
  category_id?: UUID | null;
  owner_id?: UUID | null;
  is_pending?: boolean;
  notes?: string | null;
  tag_ids?: UUID[];
}

export interface TransactionUpdate {
  amount?: Money | null;
  transacted_at?: string | null;
  posted_at?: string | null;
  description?: string | null;
  merchant?: string | null;
  category_id?: UUID | null;
  owner_id?: UUID | null;
  is_pending?: boolean | null;
  is_hidden?: boolean | null;
  review_status?: "needs_review" | "reviewed" | "ignored" | null;
  notes?: string | null;
  tag_ids?: UUID[] | null;
}

export interface ConnectionClaim {
  /** A single-use setup token, pasted from the bridge. Used once, never
   *  stored, never echoed back. */
  setup_token: string;
}

/** Pause/resume and the cadence. Absent means "no change" — neither field has a
 *  "clear it" meaning, so the panel sends only what the user touched. */
export interface ConnectionUpdate {
  is_enabled?: boolean;
  sync_interval_minutes?: number;
}

export interface SplitIn {
  amount?: Money | null;
  pct?: Money | null;
  category_id?: UUID | null;
  owner_id?: UUID | null;
  notes?: string | null;
}

// ---- investments (ADR-0011/0020/0032/0033/0034) ----
//
// The read side: the consolidated allocation and the valued portfolio. Writes
// (securities, prices, holdings, investment transactions) are not typed here yet
// because nothing consumes them — see `frontend/src/api/investments.ts`.
//
// Every number below is a decimal string, and the two scales are different:
// amounts are `NUMERIC(19,4)`, while quantities and prices are `NUMERIC(19,8)`
// because a price of `$0.00000412` is a real quote that four decimals round to
// zero. Nothing here is a JS number, by design (ADR-0005).

/** How the allocation groups its rows. The server accepts exactly these four
 *  (`ALLOCATION_GROUPS` in app/schemas/investments.py). */
export type AllocationGroup = "security" | "type" | "account" | "currency";

/**
 * Why a position could not be valued. Both are reported and **never** folded
 * into a total as zero (ADR-0032 §5): "we cannot value this" and "this is worth
 * nothing" must not render identically. They are two values because they have
 * two different fixes — enter a price, or enter an FX rate.
 */
export type UnpricedReason = "no_price" | "no_rate";

/**
 * Where a position's quantity came from (ADR-0034). `history` when recorded
 * trades exist and are therefore authoritative; `manual` when the stored scalar
 * is all there is.
 */
export type QuantitySource = "history" | "manual";

export interface AllocationRow {
  /** A UUID or a vocabulary token, always a string — the grouping is a wire
   *  vocabulary and `key`'s JSON type must not depend on `group_by`. */
  key: string;
  label: string;
  value_base: Money;
  /** Share of `total_base`, at 4 dp. */
  percent: Money;
  /** How many positions make up this row. A 3% line that is one holding and a
   *  3% line that is thirty read very differently. */
  holdings: number;
}

export interface Allocation {
  as_of: string;
  base_currency: string;
  group_by: AllocationGroup;
  /** Excludes every position that could not be valued — which is what
   *  `unpriced_positions` and `no_rate_positions` are there to say. */
  total_base: Money;
  rows: AllocationRow[];
  /** Counts, not lists. The rows cannot show a position with no price, so these
   *  are what stop a total quietly missing 30% of the portfolio. */
  unpriced_positions: number;
  no_rate_positions: number;
  max_stale_days: number | null;
}

export interface HoldingValue {
  /** **Null for a position that exists only as recorded trades** (ADR-0034).
   *  `account_id` and `security_id` identify it instead. */
  holding_id: UUID | null;
  account_id: UUID;
  security_id: UUID;
  name: string;
  ticker: string | null;
  security_type: string;
  quantity: Money;
  /** Null iff `reason` is set. Zero is a real price for a written-off position;
   *  "no price" is the absence of a row. */
  price: Money | null;
  price_date: string | null;
  price_currency: string | null;
  /** `quantity × price`, in the security's own quote currency. */
  value_native: Money | null;
  value_account: Money | null;
  value_base: Money | null;
  /** Days between the valuation date and the price used. Null when there is no
   *  price at all, which is a stronger statement than "very stale". */
  stale_days: number | null;
  /** `value_base` is null **iff** this is set. */
  reason: UnpricedReason | null;
}

export interface AccountValuation {
  account_id: UUID;
  name: string;
  currency: string;
  /** `derived`: Σ(holdings). `stated`: the account's own balance, with any
   *  remainder over the holdings reported as `unaccounted_cash_base`
   *  (ADR-0021). */
  balance_source: string;
  balance_account: Money;
  market_value_account: Money;
  market_value_base: Money;
  /** The `stated` balance in base — **what this account contributes to the
   *  portfolio total**. Null for a `derived` account (Σ(holdings) is its
   *  balance) and for a `stated` one whose balance had no rate to convert it:
   *  the account then contributes nothing to the total, and
   *  `unaccounted_cash_base` is `"0"` in that case too, so **this field is the
   *  only way to tell an unconvertible balance from a zero one**. Never render
   *  a null as `$0.00`. */
  stated_balance_base: Money | null;
  unaccounted_cash_base: Money;
  holdings: HoldingValue[];
  /** The positions themselves are in `holdings` with `reason` set. */
  unpriced: number;
  no_rate: number;
  oldest_price_date: string | null;
  max_stale_days: number | null;
  is_fully_valued: boolean;
}

export interface Portfolio {
  as_of: string;
  base_currency: string;
  /** Counts a `stated` account at its stated balance and a `derived` one at
   *  Σ(holdings) — the same rule net worth uses. */
  total_base: Money;
  accounts: AccountValuation[];
}

export interface Security {
  id: UUID;
  name: string;
  ticker: string | null;
  security_type: string;
  /** The currency the security is *quoted* in, which is half of its identity. */
  currency: string;
  is_manual: boolean;
}

/**
 * A position as recorded, before valuation (ADR-0034).
 *
 * `id`, `manual_quantity` and `manual_cost_basis` are null for a position that
 * exists only as recorded trades: it is a real position with no row behind it,
 * and nobody typed anything.
 */
export interface Holding {
  id: UUID | null;
  account_id: UUID;
  security_id: UUID;
  security: Security;
  /** The **effective** position — derived from history when any exists. */
  quantity: Money;
  cost_basis: Money | null;
  quantity_source: QuantitySource;
  basis_source: QuantitySource;
  /** What a human typed, even when history overrode it. This is the field that
   *  makes "you entered 10; three trades say 0" sayable. */
  manual_quantity: Money | null;
  manual_cost_basis: Money | null;
  as_of: string | null;
}
