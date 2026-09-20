# ADR 0036: A portable export is a document, a backup is the instance, and no id crosses between

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Related:** ADR-0005 (decimal money), ADR-0009 (the sync reconnect key), ADR-0014/0025 (RLS),
  ADR-0017/0018 (FX and transfer matching), ADR-0019 (provenance), ADR-0026 (owners are household data),
  ADR-0033 (a buy is not a transaction)

## Context

The household's data lived in one Postgres database with no way out of it that anyone would call
supported. `pg_dump` works, but it is not a product: it needs a shell on the host, it carries users and
sessions, and it cannot move one household between instances — which is the thing RLS makes *possible*
and that nothing had taken up.

Two different needs were being spoken of as one, and they want opposite designs:

- **"I want my data somewhere else, or back after I broke it."** Per-household, readable, portable,
  version-following, and safe to re-import into a household that is not empty.
- **"This instance is gone."** Whole-database, including the things that are not household data, restored
  in place, not merged with anything.

The export is the first. Backups (decision C) are the second, and the reason both ship is that neither
substitutes for the other.

## Decision

### 1. Two artifacts, not one

`GET /export` serves a versioned JSON document — `{"format": "metalmark.export", "version": 1, …}` —
whose sections are the household's tables in dependency order. `GET /export/transactions.csv` serves one
account's rows as a CSV that `services/imports` already knows how to read.

Not a zip of both. Two endpoints, each independently useful and independently testable, and the JSON one
is the format: its shape is described in `services/portability`'s column lists, not declared a second
time in a schema that a reader would then have to keep in step.

### 2. Money is a string, always

`"1234.5600"`, never a JSON number. JSON numbers are IEEE doubles; ADR-0005 is not negotiable. A reader
that sees `1200.0` refuses it *by name* rather than calling `Decimal(str(…))` and storing whatever the
float meant — that spelling is right by luck, and `0.1 + 0.2` is where the luck runs out.

### 3. A credential is not a column of the export

`account_connections.access_url_encrypted` is a live bank bearer token. It is absent from the format by
construction — the export's field lists are written out rather than read off the model, so a new column
is a deliberate decision rather than something that joins the export by default, and the credential is
the column that must never be allowed to answer that question silently.

The test is not "the exporter redacts it" but "no export byte contains it under any spelling, including
the plaintext the ciphertext decrypts to", seeded with a real `SecretBox` ciphertext so that a future
edit which decrypts the column to "helpfully" re-serialize it fails too.

A connection still exports as metadata — provider, institution, enabled, interval — and on import lands
`status = "auth_error"` with `last_error` saying the credential was deliberately not exported. Landing it
`ok` would leave a connection that reports healthy and fails on the next cron, which is the exact state
the admin panel exists to make impossible. The existing "Reconnect" affordance is already the right next
action, so no new status value is added (ADR-0028's enum stays at three).

### 4. **No id crosses the boundary, and every entity therefore has a natural key**

This is the decision the implementation forced, and it is worth stating as arithmetic rather than as
caution. **Primary keys are global while RLS hides the rows they name.** A document's uuid for a rule is
*invisible* to the target household's lookup and still *taken* at insert time. The first version of this
code kept the exported id for the entities that have no natural key, and the round-trip test failed on
`duplicate key value violates unique constraint "pk_rules"` — with the source household holding exactly
one rule and the target holding none. That is not a bug that a better lookup fixes; it is what the
combination of a global PK and a household-scoped visibility guarantee *means*.

So an `id` in the document is a **within-document reference** — how one entry names another, as a split
names its parent — and never the id the row gets here. The import allocates fresh ids and children follow
through the remap.

Every entity is matched on a key instead:

| entity | key |
|---|---|
| owner, tag | name, case-insensitive |
| category group | type + name |
| category | name within its group |
| account | `external_key`, else name + type + currency |
| security | ticker + currency, else name + currency |
| transaction, investment event | `(account, external_id)`, else the recomputed `import_hash` |
| rule | `(name, priority)` |
| connection | `(provider, org_name)` |
| transfer group | the legs it links — resolved *after* the rows |
| balance snapshot, price, holding, fx rate | its own date or quantity column |

**A key can be shared by rows a human would still call different** — two rules named "Coffee", two logins
to one bank — so each key carries an ordinal: the first document entry takes the first existing row, the
second creates one. That is the same convention `imports.import_hash` already uses for identical manual
rows, deliberately, so "the same" does not mean one thing in the CSV reader and another here.

**A transfer group is created from its legs, not from the document.** It is not a thing anyone names, so
its identity cannot be known until the rows are in; `_import_transfer_groups` therefore runs last and
creates a group only when this import created at least one row that wanted it. A group whose legs were
all matched is a link this household already has, and the document's id for it names nothing to do. When
*some* legs were matched, the new rows join the group those legs already sit in — which is what keeps a
half-imported transfer linked rather than split into two.

### 5. `base_amount` is not exported; the rates are

`base_amount` / `fx_rate_date` are a cache of `amount` at a dated rate, so the document carries the
*rates* and the import recomputes the cache for the rows it created. That is what makes an exported total
reproduce exactly rather than depending on whatever rates the target instance happened to hold — and it
is why only the rows this import created are recomputed, so an import that matched everything writes
nothing at all.

`import_hash` is not exported either, and for the same species of reason: it is a digest of the *source*
account's uuid, so in the target it is not a weaker version of the key, it is a different number. A field
that cannot be right is a field that can only mislead.

### 6. `fx_rates` is global, so the import leaves an existing rate alone

The table has no `household_id` and is unique on `(base, quote, rate_date)`. A rate is a fact about the
world; two documents that disagree about one are a data problem the second import is not qualified to
resolve, and overwriting would make the ledger's numbers depend on import order. `ON CONFLICT DO NOTHING`,
with the created count read back from `RETURNING` rather than from the number of rows offered — the
second import of a document offers every rate again and takes none of them.

### 7. The CSV is one account because the importer is

`GET /export/transactions.csv` requires `account_id`. Not a filter — the CSV importer lands every row of
a file into **one** account, so a file holding several accounts' rows would re-import them all into
whichever account the user picked: a file that says one thing and does another. Its six headers are
exactly `date, amount, description, category, owner, notes`, every one of which is a key in
`imports._SYNONYMS`, so the mapping dialog auto-suggests all six and the file goes back in through the
path that is already tested. Whole-household is `GET /export`.

### 8. Order is part of the format

Transactions are exported ordered by `(account_id, transacted_at, id)`. This is not tidiness: the import
recomputes a manual row's `import_hash` from its *ordinal among identical rows in this document*, so two
exports of the same unchanged household must list those rows in the same order or a re-import would
compute different digests and duplicate them.

### 9. An import merges, and says what it did

`POST /import` is owner-only — it can change the household's base currency and merge a second history
into the household's own. Export is available to any member, because reading the household's ledger is
what being a member already is.

The response is `created`/`matched` counts keyed by entity name plus `warnings`. A reference that cannot
be resolved is a **warning, not an error**: a rule pointing at a category the user deleted last month is
a real, importable document, and refusing the whole file over it would make an ordinary export
un-importable. The maps are **sparse** — an entity missing from `created` is one that was not created,
and a zero is never recorded. "Created 0 fx_rates" is not a smaller truth than "created no fx_rates"; it
is a different one, and the difference is exactly what a bulk insert with a conflict clause has to be
careful about.

A matched account keeps the `current_balance` it has here rather than the document's. That is the merge
case, not the restore case: an account the household has been syncing since the export has a *newer*
balance, and the document's would be a regression.

## Consequences

- **Positive:** a household moves between instances, survives a bad edit, and its CSV reaches a
  spreadsheet — with `restore-drill` (decision C) proving the other guarantee separately.
- **Positive, and the more durable half:** "no id crosses the boundary" is a rule, not a list of
  exceptions, and the natural-key table above is its whole extent. A new entity needs a key, and asking
  for one is the moment somebody notices there isn't one.
- **Negative / costs:** a household that already holds two claims to one institution and imports a
  document holding one cannot say which it means, and gets the first. This is the narrow residue of
  connection-by-key: a connection is the row a user must *reconnect*, so a duplicate is not a harmless
  extra row but a second "paste your token" prompt for an institution already connected, arriving afresh
  on every import.
- **Negative / costs:** `_digest` reimplements `imports.import_hash` rather than calling it, because the
  two read ordinals from different places. A unit test pins them equal, which is the only thing keeping
  two spellings of one payload honest.
- **Negative / costs:** an import is one transaction, so a large document holds a long-lived write
  transaction and a failure rolls the whole thing back. That is the right default — a half-imported
  household is worse than a refused one — but it means the 64 MB ceiling is a real ceiling rather than a
  tuning knob.
- **Not done, deliberately:** users, sessions, membership and sync jobs/runs/events are excluded. They
  are operational rather than household data, and a document that carried them would be a backup wearing
  an export's clothes.
- **Not done, deliberately:** a merge policy for conflicting *values* on matched rows (whose note wins,
  whose category wins). Matched means matched: the row that is already here is left alone, and the
  document fills in what is missing rather than overwriting what is present.
