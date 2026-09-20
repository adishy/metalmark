# ADR 0027: Open signup replaces invite-only

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Supersedes:** ADR-0013 (**invite half only** — argon2id, server-side sessions, cookies and CSRF stand)
- **Related:** ADR-0002 (LAN/VPN only — now load-bearing for authorization), ADR-0012/0026, ADR-0014,
  ARCHITECTURE.md §2, §5, PLAN.md

## Context

ADR-0013 chose invite-only signup: an admin issues an invite (`invites` table, hashed token, expiry), and
signup requires a valid one. In practice the instance serves one household of a few people over LAN/Tailscale
(ADR-0002), so the invite flow was ceremony — an existing owner had to be logged in to hand a link to somebody
already on the VPN. It also cost a second token system to hash, expire and accept, and its link landing was the
one thing forcing `SameSite=Lax` instead of `Strict` on the session cookie (ADR-0013 flagged that itself).
Meanwhile ownership stopped being users at all (ADR-0026), so "an admin invited a user" no longer described
who exists in the ledger.

## Decision

- **Delete the invite flow.** `POST /auth/invites`, the `invites` table and the invite token in signup are gone.
- **`POST /auth/signup {email, display_name, password, household_name?}`** — if the users table is **empty**,
  create the household and make the signer its `owner` role member and `is_admin`; otherwise join the
  **oldest** (first-created) household as a `member`.
- **"Join the oldest household" is the entire access model.** No invite codes, no admin approval, no
  household picker: the first signer bootstraps the household, everyone after them lands in it. It needs no
  configuration and no new table, which is the whole appeal at this scale.
- **Concurrent first-signups are serialized with a Postgres advisory transaction lock** (the same mechanism
  class as the sync-job claim, ADR-0004). Without it, two simultaneous first signups can each observe an empty
  users table and each create a household — two founders, two households, one of them invisible.
- **`METALMARK_OPEN_SIGNUP=false` closes signup** (an env safety valve: refuse the endpoint entirely, keeping
  existing sessions valid). It is read from the environment, so closing signup is a restart, not a live toggle.

## Consequences

- **Positive:** less code and one fewer token system to get wrong — nothing to leak, expire, or accept; a new
  member (or a replacement phone after a locked-out password) self-serves over the VPN; no invite landing page,
  so the `SameSite=Strict` friction ADR-0013 noted disappears.
- **Negative / costs — stated plainly:** **anyone who can reach the instance can create an account and join the
  household**, and because ownership drives *views*, not *access* (ADR-0012's surviving half, ADR-0026), that
  account can read every account, transaction, balance and report in the household. This is acceptable **only**
  because the API is reachable exclusively over LAN/Tailscale (ADR-0002): the network is now the *only* thing
  between a stranger who can route to the box and the household's finances. There is no defense in depth left
  here — invite-only was the last app-layer gate and it is deliberately gone. Accordingly, **anyone who exposes
  the API (or widens the tailnet) must set `METALMARK_OPEN_SIGNUP=false` first**, and ADR-0002 is no longer
  just an attack-surface decision: it is load-bearing for authorization, so changing it re-opens this ADR.
  Secondary cost: there is no approval step, so a household owner cannot see or reject a join before it
  happens — the DB row (and the audit log) is where it becomes visible, after the fact.
- **Follow-ups:** an integration test for the concurrent-first-signup path (advisory lock ⇒ exactly one
  household); `METALMARK_OPEN_SIGNUP` documented in `.env.example` and the ops notes; signup recorded in
  `audit_log` as a sensitive action; revisit if the deployment model ever leaves the LAN/VPN boundary.
