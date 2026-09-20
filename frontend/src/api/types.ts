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

export interface SplitIn {
  amount?: Money | null;
  pct?: Money | null;
  category_id?: UUID | null;
  owner_id?: UUID | null;
  notes?: string | null;
}
