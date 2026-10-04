# ADR 0062: Agents can add accounts, and read account files as they are

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** ADR-0048 (agent privacy), ADR-0060 (account files), ADR-0061 (agent entry scopes)

## Context

An agent that enters transactions and holdings (ADR-0061) could only do so in accounts a person had
already created. The owner also wants an agent to read the files kept beside an account — a statement
is where the numbers the agent is asked to enter come from.

Both push against ADR-0048. A new account needs a name, and a name an agent chooses and reads back is
a dictionary for pseudonyms. A file cannot be anonymized at all: it is whatever was uploaded.

## Decision

Two more opt-in token scopes, each issued only by the household owner and re-checked against the
issuer's current owner role on every request.

**`accounts:write`** — `POST /agent/v1/accounts` creates one manual account. Its request schema
(`AgentAccountCreate`) refuses unknown fields and takes a type, a currency, a subtype from the fixed
list the mirror already shows unaltered, an optional signed opening balance and its date, and an
existing owner id. **An agent cannot name the account or its institution.** The account is created as
`Added by agent`, or `Added by agent: <label>` with an optional label of at most 80 characters, and a
person renames it. As with ADR-0061's note, the fixed prefix marks the row for the household and means
the stored name can never equal a name the household wrote, so its pseudonym matches nothing. An
investment account takes no balance: its value is the sum of its holdings. The response is the
anonymized account, with the real id the other write routes take. No updates, no deletes; refusals are
generic and every write and refusal is logged as `agent.write`.

**`documents:read`** — `GET /agent/v1/accounts/{account_id}/documents` lists an account's files, and
`GET …/documents/{document_id}/content` returns one file's bytes exactly as uploaded, with its stored
media type and its SHA-256 in `X-Content-SHA256`. **These two routes are not anonymized**, and say so
in discovery, in OpenAPI and on the permission's label in the app: file names are real and contents
are whatever the file holds. They run in read-only transactions under the household's RLS. The scope
grants nothing else: it does not open the mirror, and `agent:read` does not open the files — the
mirror still answers the app's document routes with "not exposed". Each access is logged as
`agent.file` with the token id. The server does not extract or convert anything; an agent reads a PDF
as a PDF.

## Consequences

A token carrying `documents:read` sees real names and numbers. Combined with `agent:read` on the same
token, it can also tell which pseudonymous account a real statement belongs to. That is what the owner
is granting, and the reason the scope is separate, owner-only and labelled as not anonymized. A token
without it is exactly as anonymous as before.

A file is returned whole, up to the 100 MiB one file may be (ADR-0060); there are no range requests.

Agent-created accounts carry a placeholder name until a person renames them, and a retried POST makes
a second account. As with ADR-0061 there is no rate limit and no idempotency key.
