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
  owner_user_id: UUID | null;
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
  owner_user_id: UUID | null;
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
}

export interface CashFlowPoint {
  month: string;
  income: Money;
  expense: Money;
  net: Money;
}

export interface SpendingReport {
  base_currency: string;
  rows: { category_id: UUID | null; category_name: string; total: Money }[];
  total: Money;
}
