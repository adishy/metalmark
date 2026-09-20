# ADR 0030: OFX/QFX import — 2.x only, FITID-first dedupe, banking rows only

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** household + Claude
- **Related:** ADR-0005, ADR-0007, ADR-0009, ADR-0019, ADR-0022, `docs/PLAN-v0.9.md` decision J, `services/imports.py`

## Context

`api/imports.py:29` has carried the same comment since Phase 0 — *"The OFX/QFX sibling lives here when Phase 2
grows it (ADR: defused XML)"* — and `defusedxml>=0.7.1` has been in `pyproject.toml` for just as long with
**no call site anywhere in `app/` or `tests/`**. It is pre-seamed the way `SecretBox` was: the decision about
XML parsing was taken before the code existed, and this ADR is where the rest of it is taken.

Two things make "OFX" a harder target than the single name suggests.

**OFX is two incompatible file formats.** 1.x is SGML: an unquoted, colon-delimited header block
(`OFXHEADER:100`, `DATA:OFXSGML`) followed by tags whose values are not always quoted and not always closed.
2.x is XML with a real declaration. They share a tag vocabulary and nothing else. Any parser that reads one
does not read the other, and the SGML dialect has no specification strict enough that two implementations
agree on where a value ends.

**Bank exports overlap, and descriptions are not stable.** The dedupe key CSV uses is
`import_hash(account_id, transacted_at, amount, description, ordinal)` (`services/imports.py:414`) — a hash
over the row's content. For a downloaded statement that is the right key: the same file re-imported
reproduces every digest. For OFX it is the wrong key, because OFX carries something better. `<FITID>` is the
institution's own identifier for the transaction, it is stable across downloads, and it survives the thing
that breaks content hashing — a bank correcting a description between two exports.

The shape to follow already exists: `commit_csv` parses, dedupes in one query, loads rules once, and creates
each row inside a savepoint. OFX needs the same skeleton with a different parser and a better key.

## Decision

**1. OFX 2.x and QFX only. OFX 1.x is detected and refused, by name.**

Detection reads the first non-whitespace bytes: `OFXHEADER:` means 1.x, `<?xml` or `<?OFX` means 2.x, neither
means this is not an OFX file at all. A 1.x upload gets a `LedgerError` that names the format and the way out
— most banks that still emit 1.x also offer QFX or CSV, and the message says so.

Refusing precisely beats accepting approximately. To read 1.x with an XML parser you must first *guess* where
unquoted values end; a guess that mis-splits one `<NAME>` silently corrupts a merchant, and a guess that
mis-splits one `<TRNAMT>` silently corrupts an amount. This is the one class of bug in this workstream that
lands wrong money in the ledger with no error anywhere, and it is not worth a compatibility win that the
CSV path already covers.

**2. Parsing is `defusedxml.ElementTree`, on size-bounded bytes.**

`MAX_FILE_BYTES` is enforced before the parse, exactly as CSV does it. `defusedxml` forbids DTDs and entity
expansion, which is the entire attack surface of a user-supplied XML file: `xml.etree` alone runs on expat
with internal entity expansion enabled, so a nested-entity document expands to gigabytes from a few hundred
bytes. The dependency is already declared; this gives it its first call site.

**3. Dedupe is FITID first, `import_hash` second.**

When a transaction carries a `<FITID>`, it is written to `Transaction.external_id` — the column and its
partial unique index already exist for sync (ADR-0009), so this adds a *writer*, not a column. When a file
carries no FITID, the row falls back to `import_hash`, computed exactly as CSV computes it. Both indexes stay
in force, so an overlapping re-export is a skip on either key rather than a duplicate on whichever one the
bank happened to break.

The failure this prevents is concrete: export January, export January–February, import both. Every January
row appears in both files. With FITID that is one skip each; with a content hash it is one skip each *until*
the bank rewrites a description, at which point the same transaction lands twice and nothing reports an error.

**4. The human picks the account; the file's `<ACCTID>` is shown, not obeyed.**

The preview reports the file's `<ORG>`, `<ACCTID>`, `<CURRENCY>` and date span, and commit takes an explicit
`account_id` — the same contract CSV has. Auto-matching on `ACCTID` would mean inventing a persistent
account-mapping concept, and ADR-0009's position is that the ledger belongs to the household rather than to
whatever produced a file. Showing the fields gives the user everything auto-matching would have given them
(confirmation that this is the right statement) without the concept.

**5. Banking transactions only. Investment rows are counted and reported as skipped.**

`<BUYSTOCK>`, `<SELLSTOCK>`, `<BUYMF>`, `<INVBANKTRAN>` and their siblings inside `<INVTRANLIST>` are counted
and surfaced in the commit result — *"N investment transactions skipped"* — with a line saying investments
land in the next workstream.

They are not imported as cash rows. A `<BUYSTOCK>` is not a cash outflow: importing it as one double-counts
the movement the moment investments exist as first-class objects (ADR-0011), because the cash leg and the
security leg are two views of one event. Counting them is what makes an all-investment file import honestly
as "0 imported, 40 skipped" instead of a silent success that looks like a bug.

**6. `<LEDGERBAL>` writes a balance snapshot** through the same upsert the sync path uses, so an imported
statement and a synced statement cannot disagree about what a balance snapshot means.

**7. Rules run on imported rows** — `load_rules` once per file, applied per row, exactly as CSV does it. A
downloaded statement is precisely the artefact a rule is written for.

## Consequences

- **Positive:** overlapping exports dedupe on the institution's own key rather than on a hash of a
  description the bank may rewrite; a file that cannot be read safely says why instead of importing a wrong
  number; `defusedxml` stops being a declared-but-unused dependency; the CSV and OFX paths share their
  dedupe, rule-loading and savepoint discipline, so the two importers cannot drift.
- **Negative / costs:** a household whose only bank emits OFX 1.x keeps using CSV. Accepted, and the error
  message is the mitigation. Investment rows are skipped outright, so an investment-only file imports as an
  empty success — the reported count is what makes that legible rather than confusing, and it becomes real
  import behaviour once item 1 of `PLAN-v0.9.md` lands.
- **`external_id` gains a second writer.** Its uniqueness contract is now shared between the sync engine and
  the importer. Both write through the same partial unique index, so a collision is a counted skip rather
  than a 500 — but any future change to that index has two callers to consider, not one.
- **Follow-ups:** OFX 1.x support is a separate decision if it is ever wanted, and it needs its own parser
  rather than a preprocessing step. Investment-row import (`INVTRANLIST` → `investment_transactions`) is
  deferred to the investments workstream, where it belongs.
