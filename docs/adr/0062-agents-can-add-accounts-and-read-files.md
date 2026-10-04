# ADR 0062: Agents can add accounts, and read account files as they are

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** ADR-0048 (agent privacy), ADR-0060 (account files), ADR-0061 (agent entry scopes)

## Context

An agent that enters transactions and holdings (ADR-0061) could only do so in accounts a person had
already created. The owner also wants an agent to read the files kept beside an account — a statement
is where the numbers the agent is asked to enter come from.

Both push against ADR-0048. A new account needs a name, and a name an agent chooses and reads back
can be compared with the pseudonyms of the household's own names. A file cannot be anonymized at all: it is whatever was uploaded.

## Decision

Two more opt-in token scopes, each issued only by the household owner and re-checked against the
issuer's current owner role on every request.

**`accounts:write`** — `POST /agent/v1/accounts` creates one manual account. Its request schema
(`AgentAccountCreate`) refuses unknown fields and takes a type, a currency, a subtype from the fixed
list the mirror already shows unaltered, an optional signed opening balance and its date, an existing
owner id, and an optional name. An investment account takes no balance: its value is the sum of its
holdings. The response is the anonymized account, with the real id the other write routes take. No
updates, no deletes; refusals are generic and every write and refusal is logged as `agent.write`.

**An agent may name the account it creates.** This is the one exception to ADR-0061's "ids, numbers
and dates — never names", and the owner chose it knowingly: an account called `Added by agent` that
has to be renamed by hand each time is not worth the protection it buys. The name is at most 200
printable characters; without one the account is `Added by agent`. The institution still cannot be
set.

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

**The account name is a pseudonym oracle, and that is accepted.** A pseudonym is keyed on the value it
hides, and account names take precedence among known names. An agent holding both `accounts:write`
and `agent:read` can create an account with a guessed name and see whether the pseudonym it reads
back also appears elsewhere: one account per guess tells it whether the household has an institution,
owner, security, category or tag of exactly that name. While that account exists, that other thing is
also shown under the account's pseudonym. Each guess leaves a visible account only a person can
delete and an `agent.write` log line, and the scope is owner-issued. Descriptions, merchants and notes
are not affected: transactions still take no free text. Closing this would need pseudonyms for
agent-authored rows keyed on the row rather than the value, as ADR-0061 already notes.

A retried POST makes a second account. Because a manual account is restored from an export by its
name, type and currency (ADR-0036), two agent-made accounts sharing all three would merge on import
and one would lose its balance. So the route numbers a name that an account of the same type and
currency already has: `Added by agent`, `Added by agent 2`. Accounts a person creates are not
renamed; ADR-0036's rule for those is unchanged. As with ADR-0061 there is no rate limit and no idempotency key.
