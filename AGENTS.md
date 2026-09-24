# Working on MetalMark Money

The name people see is **MetalMark Money**; the code, database, env vars and images say
`metalmark`. A self-hosted personal-finance app for one household: FastAPI + Postgres (RLS) +
React/TS. `frontend/AGENTS.md` and `backend/AGENTS.md` hold the rules for each half; read the
one for the tree you are changing.

## Before changing anything

- **Decisions live in `docs/adr/`.** Read the ADRs a change touches. A change to an `Accepted`
  decision needs a new ADR that supersedes it (rules in `docs/adr/README.md`). New ADRs land
  in the same PR as the change.
- **The data is real.** Every migration and data change must be safe on a populated database
  (see `backend/AGENTS.md`).
- **Sessions are recorded** in `agent_record/` — one file per session, append-only.

## Gates

`./scripts/verify.sh` runs every CI gate locally (`--list` shows them; `reset` rebuilds the
seeded dev stack). CI runs the same scripts. For screenshots and quick e2e runs against a
throwaway demo household, `./scripts/dev-stack.sh up` (see the script's header).

A change is done when its gates pass **and**, for anything a person sees, it has been looked
at: `frontend/AGENTS.md` says how.

## Debugging a running instance

Through the anonymized agent API (ADR-0048), never through the database:
`docs/runbooks/debugging-a-live-instance.md`. Treat the agent token as a secret — read it into
a variable, never print it, never commit it.

## Commits and PRs

Branch from `main`; one topic per PR. The PR description says what changed, why, how it was
verified, and anything data-affecting (migrations, backfills).
