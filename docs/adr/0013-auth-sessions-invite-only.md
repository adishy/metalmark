# ADR 0013: Auth — server-side sessions, argon2id, invite-only

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0012, ADR-0014, ARCHITECTURE.md §2, §5

## Context

A few trusted users, no public sign-up. We hold financial data, so we want revocation/logout-everywhere and
sane expiry — not stateless tokens we can't invalidate.

## Decision

We will use **email + password with argon2id**, **server-side sessions** (a `sessions` table) delivered via
**httpOnly + SameSite cookies** with a per-session CSRF token, login rate-limiting/lockout, and
**invite-only** signup (admin issues invites; tokens stored hashed). No 2FA in v1 (accepted risk given
Tailscale + invite-only; logged in ADR/security notes).

## Consequences

- **Positive:** sessions are revocable; idle + absolute expiry; no open registration; strong hashing.
- **Negative / costs:** server-side session store to manage; SameSite=Strict may break invite-link landing —
  use Lax there. No 2FA is a deliberate, documented risk.
- **Follow-ups:** tenant isolation is a separate concern (ADR-0014); revisit 2FA if exposure changes.
