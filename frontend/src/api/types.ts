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

export interface NetWorthSeries {
  base_currency: string;
  points: { date: string; net_worth: Money }[];
  delta_net_worth: Money;
  net_cash_flow: Money;
  currency_revaluation: Money;
  /** Ownership only ever moves whole accounts, never individual postings. */
  attribution: "account";
}

export interface CashFlowPoint {
  month: string;
  income: Money;
  expense: Money;
  net: Money;
}

export interface CashFlowSeries {
  base_currency: string;
  points: CashFlowPoint[];
  /** Every entry stands on its own owner — the mirror of net worth's "account". */
  attribution: "row";
}

export interface SpendingReport {
  base_currency: string;
  rows: { category_id: UUID | null; category_name: string; total: Money }[];
  total: Money;
  /** Same row-level reading as cash flow. */
  attribution: "row";
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
  current_balance?: Money;
  balance_date?: string | null;
  /** Omit to let the server assign the Shared owner. */
  owner_id?: UUID;
}

export interface AccountUpdate {
  name?: string;
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
