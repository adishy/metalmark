# OFX/QFX fixtures

ADR-0022 draws a line between **captured** fixtures (a real artefact from the real
system, trimmed only in volume) and **constructed** ones (hand-written to encode an
assumption). This directory is entirely on the constructed side, and deliberately so:

**Every file here is hand-authored.** There is no captured OFX file, for two reasons.

1. A real statement is real financial data — account numbers, balances, merchant
   names, dates. ADR-0022's capture rule exists for *wire formats* (see
   `../simplefin/README.md`, where the capture proved six things the docs got
   wrong). It does not exist to put a household's ledger into a git repository.
2. The wire format here is not in doubt the way SimpleFIN's was. OFX 2.x is a
   published XML dialect with a published tag vocabulary, and the parser
   (`app/services/ofx.py`) reads a *documented* structure rather than reverse
   engineering a moving target.

So these files encode our **behavioural** assumptions. Each one names the
assumption it holds, and a fixture that disagrees with a real bank file is a bug
report waiting to happen — which is the honest trade for not shipping a ledger
into a repo.

| File | What it holds |
|---|---|
| `statement.ofx` | A normal OFX 2.x checking statement: four rows, `FITID` on every one, one row with no `<MEMO>`, one with a `[-5:EST]` zone suffix, and a `<LEDGERBAL>`. |
| `statement.qfx` | The QFX shape: no XML declaration, a `<?OFX …?>` processing instruction instead, one row, no zone suffixes, February dates. |
| `legacy.ofx` | **OFX 1.x SGML.** Unquoted values, unclosed tags, a `OFXHEADER:100` header block. Not parsed — refused, by name (ADR-0030 §1). |
| `malformed.ofx` | Well-formed intent, broken XML: unclosed elements, truncated mid-file. |
| `entities.ofx` | A DTD declaring a nested entity chain (`lol`…`lol9`, the textbook billion-laughs). Well-formed XML, refused with a 400 because the parser is `defusedxml`. Note it is *not* the case that pins the dependency: CPython 3.12's expat refuses a nested bomb on its own (an input-amplification limit), so this fixture would be refused by both parsers. The load-bearing case — one long entity, expanded many times over, which the standard library happily returns — is built inline in `tests/unit/test_ofx_parse.py::test_defusedxml_is_the_load_bearing_part_not_the_stdlib`. |
| `january.ofx` | January's export: three rows. |
| `january_february.ofx` | The overlapping re-export: the same three rows with the **same FITIDs** — one of them with its memo *corrected* — plus two February rows. This is ADR-0030 §3's whole point: `import_hash` differs for the corrected row, `FITID` does not. |
| `no_fitid.ofx` | A file with no `<FITID>` anywhere, including two genuinely identical rows. The `import_hash` fallback's test: the pair must both import, the re-import must not. |
| `investments.ofx` | An `<INVSTMTRS>` whose `<INVTRANLIST>` holds five investment rows (`BUYSTOCK`, `BUYMF`, `SELLSTOCK`, `INCOME`, `<INVBANKTRAN>`) and no banking rows at all. Imported as "0 imported, 5 skipped" (ADR-0030 §5). |

Amounts, account ids, balances, dates and merchant names are invented. The bank
("Harborline Credit Union") does not exist.
