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
| 2026-09-23 | 05 | Balance-model fixes (0043/0044), balance history + coverage (0045), chart, stale/alike accounts, daily FX fetch (0046) |
| 2026-09-24 | 06 | Cash flow debugged via the agent API; typed starter categories, local categorizer, Accounts redesign, balance history, institution logos (0049) |
| 2026-09-24 | 07 | Design review (-ink tokens), phone UI overhaul, stalled-bank diagnosis (0050), repo AGENTS.md files |
| 2026-09-24 | 08 | The bank's holdings landed as positions (ADR-0051, migration 0011); worktree + CI notes |
| 2026-09-25 | 09 | Eight improvements: connection names, Insights + allocations with bank cash, owner income and paystubs, recurring, fixed PWA chrome, a chart pass, an Amazon spike; seven topics landed in one merge |
| 2026-09-26 | 10 | Every open issue at once: the correctness audit (F1–F5), the desktop/design pass (17 issues), budgets (0058), two stacked PRs plus a tooling one, and the two reviewer agents — which then found seven defects in code that had already passed every gate |
| 2026-10-10 | 13 | The net-worth chart's value axis follows its figures instead of starting at zero, with a floor so cents are not magnified; its date labels are chosen from the series' own points and spaced to the plot |
