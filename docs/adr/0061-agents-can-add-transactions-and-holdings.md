# ADR 0061: Agents can add transactions and holdings with separate permissions

- **Status:** Accepted
- **Date:** 2026-10-04
- **Supersedes:** ADR-0048, read-only-only scope (GET/debug remain read-only)

## Context

The owner wants agents to enter transactions and add account holdings without granting general write access. Existing read/debug tokens must retain their security boundary.

## Decision

Add opt-in `transactions:write` and `holdings:write` token scopes. Only explicit POST routes for transaction creation and holding creation accept these scopes. Both can be issued only by the household owner and are re-checked against the issuer's current owner role on every request. They use the same household-scoped services as the browser. Existing holdings are rejected, including bank and history positions; no updates, deletes, security management, trades or files are exposed. Reads and debug requests retain database-enforced read-only transactions. Existing token scopes are unchanged.

**An agent submits ids, numbers and dates — never names.** A pseudonym is keyed on the value it hides (ADR-0048), so any text an agent could both choose and read back is a dictionary: post a transaction with merchant "Safeway", read it back, and compare the pseudonym with the real rows'. The same holds for a known person's or account's name, and for anything the ledger matches or groups by (rules, recurring series). ADR-0048 refuses free-text query parameters for this reason, and a write is the same question asked another way. So the write routes have their own request schemas (`AgentTransactionCreate`, `AgentHoldingCreate`), which refuse unknown fields:

- A transaction takes account, signed amount, dates, category, owner, tags and pending — no description and no merchant.
- A holding takes account, existing security, quantity, cost basis, as-of and an optional total market value — no local name, symbol or type.
- A transaction may carry one optional `note` of at most 500 characters. It is stored as `Added by agent: <note>` (or just `Added by agent`), so the household can always see which rows an agent entered, and so the stored value can never equal anything the household wrote: its pseudonym matches nothing and it is never looked up as a known name.

Inputs address real IDs, never pseudonyms. Amounts, quantities and dates are bounded to what the columns hold and to a sane window, so a bad value is a 422 rather than a database error. A category, tag, owner, account or security outside the household is a generic 404. Responses use the existing anonymization policies, and every refusal is generic. Validation responses never echo submitted values. Every write and every refusal is logged as `agent.write` with the token id, route and status. Discovery describes writes and links to their OpenAPI schemas, which state the sign convention and currency of each number.

## Consequences

Transactions created by a retry are separate entries; callers must check returned IDs before retrying. An agent-entered transaction on a bank-synced account is, like a hand-entered one, not matched against the bank's own copy when that arrives: both rows will exist until a person removes one. Holding creation uses the service's create-only path and rejects an existing position. Concurrent inserts are rejected by the unique account/security constraint and roll back with a generic conflict response. Agent writes preserve user provenance and participate in normal ledger rules; with no description or merchant, only rules conditioned on amount or account can match them. No migration or automatic permission upgrade is needed.

An agent-entered transaction has no description until a person adds one. That is the cost of keeping the anonymized mirror anonymized, and it is deliberate: lifting it needs pseudonyms for agent-authored rows that are keyed on the row rather than the value, and those rows kept out of every match and grouping, which is a separate decision.

There is no rate limit and no idempotency key. A write-scoped token can add rows without bound and only a person can delete them; the `Added by agent` note and the `agent.write` log are how they are found.
