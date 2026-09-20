# ADR 0012: Household + view-filtering sharing (no per-account access control in v1)

- **Status:** Superseded by ADR-0026
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0013, ADR-0014, ARCHITECTURE.md §2, §4, §5

## Context

The users are a small, trusted group, some with joined finances. Monarch's "Shared Views" model assigns
ownership to accounts/transactions to personalize views while keeping a joint picture. A stricter model —
accounts a household member genuinely cannot see — is more work and a real access-control system.

## Decision

For v1, a user belongs to one **household**; all data is scoped to it. Ownership (`owner_user_id` on accounts,
per-split owner) drives **views/filters** ("mine", "partner's", "joint"), **not access** — every household
member can read all household data. Truly private, member-hidden accounts are explicitly out of scope for v1.

## Consequences

- **Positive:** matches Monarch; simple and correct for joined finances; less authorization surface.
- **Negative / costs:** no privacy between household members — the UI must not imply privacy it can't enforce.
- **Follow-ups:** if private accounts are later needed, that's a new access-control model and a superseding ADR.
