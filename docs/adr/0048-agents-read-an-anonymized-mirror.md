# ADR 0048: Agents read an anonymized, read-only mirror of the app's API, by token

- **Status:** Accepted
- **Date:** 2026-09-23
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0013/0027 (auth, open signup), ADR-0014/0025 (RLS, identity tables outside it), ADR-0016
  (nothing secret or personal in a sync log), ADR-0007 (field provenance), ARCHITECTURE.md §5

## Context

The owner works on this app with AI agents, and wants them to be able to read the app's state: to answer
questions about the household in a structured way now, to write later, and to debug the app by seeing
what a page shows and why. Two things the app already has make that hard:

- **The only credential is a browser session.** An agent cannot hold one sensibly (a cookie, a CSRF
  token, the owner's password), and it would be the owner's whole identity rather than a revocable key.
- **Everything the app returns is personal.** Account names carry the owner's name and the account
  number's tail; a transaction's description is whatever the bank wrote ("ZELLE TO JANE DOE"); notes are
  free text. A debugging agent does not need any of it, and a log, a transcript or a model provider is not
  a place the household's names belong.

What an agent *does* need is the rest, exactly: ids, amounts, dates, statuses, provenance, and the app's
own arithmetic. The bugs are almost all numeric (ADR-0043/0044/0045 were all found in the numbers).

## Decision

We will add two surfaces for agents, `/api/agent` and `/api/anon_debug`, authenticated by **agent tokens**
and answering with **anonymized, read-only** data.

1. **Tokens.** An administrator, or the household's owner, issues a token in Admin → Agent access with a
   name, one or both scopes (`agent:read`, `debug:read`) and an expiry of 1–365 days (no "never"). The
   token (`mmk_…`) is shown once; the server keeps its sha256, like a session's. `agent_tokens` is an
   identity table like `sessions` — no `household_id`, no RLS — and the household is the issuer's,
   resolved through `household_members` on every request. A token works only while its issuer may still
   issue tokens: demoting or removing them switches it off. Revoking is immediate.

2. **Separate doors.** The agent routes read `Authorization: Bearer` and never the session cookie; the
   app's routes read the cookie and never `Authorization`. Neither credential opens the other's routes.

3. **One code path.** `/api/agent/v1/<path>` is the app's own `GET /<path>`, run in-process as the
   token's issuer (an ASGI sub-request carrying the grant in its scope, which no client can set), and
   `/api/anon_debug/view?path=<url>` is the same for a URL pasted from the browser. There is no second
   implementation of any report, so what an agent sees is what the page sees. Every app `GET` route is
   exposed except a short, reasoned `EXCLUDED` list (`/auth/me`, the raw exports, `/healthz`).

4. **Read-only in the database.** Every agent request runs in a `SET TRANSACTION READ ONLY` transaction,
   and the grant refuses any method but `GET`. Read-only is a property Postgres enforces, not a list of
   routes someone keeps.

5. **Anonymized on the server, by allowlist.** Every field an agent can receive has a policy in
   `app/agent/policies.py`; a field with none is dropped, and a test fails until it has one. The policies:
   ids, amounts, dates, counts and flags are **kept**; names and every piece of free text (accounts,
   owners, institutions, securities, merchants, descriptions, notes, custom categories and tags, people)
   become **pseudonyms** — `Account 3f9a2c`, an HMAC of the value under a key derived from the app secret
   and the token id, so stable for a token (equal pseudonyms mean equal values) and unjoinable across
   tokens; generic category words ("Groceries") and the Shared owner are kept; sentences the app writes
   (report warnings, check summaries) keep their words with the household's names **substituted** by
   those same pseudonyms and number shapes (card and account numbers, SSNs, e-mail addresses) masked;
   text from outside (a bank's error) additionally loses anything shaped like a name; e-mail addresses
   and the CSRF token are dropped. A `Keep` policy on a string field is refused by a test.

6. **No oracles.** A free-text query parameter would leak what it matches whatever the response hides
   (`search=Jane` returning one row instead of none), so string parameters pass only if named with a
   pattern (`review_status`, `cursor`, `group_by`, `granularity`); `search` is refused. App error messages
   are not echoed (a 409 can interpolate an owner's name); an agent gets the status, a code and a hint.

7. **Self-describing.** `GET /api/agent` (and `/api/agent/v1`) and `GET /api/anon_debug` return a catalog of
   every route with its parameters, return type, description and — where it needs no id — a URL to call.
   It is built from the routes themselves, and a test crawls it.

8. **Debug views** compose app routes with a few read-only queries: `transactions/{id}/explain` (provenance,
   every rule evaluated condition by condition by the engine's own evaluator, the owner chain, the
   transfer), `accounts/{id}/balance` (snapshots, drift since the last one, holdings, the checks that flag
   it), `system` (versions, non-secret settings, counts, FX coverage, connection health), and `pages/{page}`
   (every request a page makes, as the page gets it).

Writes, when they come, get their own scope and a dependency without `READ ONLY`, and a pseudonym is not
an input: they address rows by id, which is why ids are kept.

## Consequences

- **Positive:** an agent can read and debug the whole app without any of the household's names, and
  without a second implementation to drift from the first. The anonymizer fails closed — a new field is
  invisible to agents until it is classified. Tokens are revocable, scoped, expiring, and die with their
  issuer's role.
- **Negative / costs:**
  - **Anonymized is not harmless.** Amounts, dates and the shape of the household's finances are real by
    design; a leaked token is a leaked financial picture. Treat tokens as secrets; keep them short-lived.
  - **Text substitution is a scrubber**, and only as good as the names it knows. It applies to text the
    app writes (whose interpolations are the household's own names) and, with the proper-name mask, to a
    bank's error text. A bank message naming someone the household never told the app, in lower case, would
    survive. Free text a *person* or a *bank* wrote into a field is never scrubbed — it is pseudonymized
    whole.
  - A pseudonym's kind is the first kind the name is known by: a rule named "Safeway" makes the merchant
    "Safeway" read `Rule …`. Consistency across fields was preferred to a kind label that changes.
  - Pseudonymizing merchants and descriptions means an agent cannot read *why* a row was categorized from
    its text; the explain view answers that on the server instead.
  - The in-process sub-request is a second trip through the middleware stack per call.
- **Follow-ups:** write scopes (a new ADR); consider a per-token request log if agents become routine; the
  Admin page is still shown to `is_admin` only in the client, while the server also lets the owner manage
  tokens.
