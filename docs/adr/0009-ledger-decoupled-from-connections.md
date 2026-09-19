# ADR 0009: The ledger is decoupled from connections

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0001, ADR-0010, ARCHITECTURE.md §2, §3

## Context

Aggregator connections are flaky (Monarch's #1 complaint): they break on password change, MFA, or token
expiry, and re-adding them often mints new account/transaction IDs — causing duplicate accounts and history.
The owner's requirement: integration health may come and go, but the ledger must stay consistent and
recoverable without ever manually restoring transactions.

## Decision

**Transactions, accounts, holdings, and snapshots belong to the household, not to a connection.** A connection
is disposable sync metadata: `accounts.connection_id` is nullable `ON DELETE SET NULL`, and transactions never
reference a connection. Removing a connection preserves all history (its accounts become manual); re-adding
**remaps** to the existing accounts by `external_key` (org + account number/name) and resumes.

## Consequences

- **Positive:** reconnects and provider hiccups never destroy or duplicate data; kills Monarch pain points #1/#2.
- **Negative / costs:** remap matching must be robust (institutions rename accounts); a bad match could attach
  to the wrong account — mitigated by conservative keys + a manual confirm step on ambiguity.
- **Follow-ups:** acceptance test — remove + re-add a connection loses zero transactions and creates zero dupes.
