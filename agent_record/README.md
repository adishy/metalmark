# Agent Record

Chronological records of AI-agent working sessions on Kestrel — the dialogue, decisions, research, and
reviews behind the artifacts in this repo. This complements `docs/adr/` (which captures *decisions*) by
capturing the *process*: what was asked, what was researched, what the reviews found, and why choices were made.

- One file per session, named `YYYY-MM-DD-session-NN-<topic>.md`.
- Records are **append-only history** — don't rewrite a past session; add a new record.
- Reconstructed from the conversation in-session (not a byte-exact chat export), so wording of assistant
  narration is paraphrased; user messages, decision options, and review findings are reproduced faithfully.

## Index

| Date | Session | Topic |
|---|---|---|
| 2026-09-19 | 01 | Architecture + multi-agent plan; two design-review passes; ADR strategy |
| 2026-09-19 | 02 | Implementation (M1a build): P0 foundations + test/CI workstream |
| 2026-09-20 | 03 | Kestrel → MetalMark rename; ownership reshape (ADR-0026); open signup (ADR-0027) |
| 2026-09-23 | 04 | Reports audit: why net worth swings (liability sign, derived investments, first-snapshot cliff) |
