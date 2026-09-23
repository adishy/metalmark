# ADR 0047: An upgrade is a pull and a restart; the deployment backs up, migrates and verifies itself

- **Status:** Accepted
- **Date:** 2026-09-23
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0039 (the deployment is standalone; images come from GHCR), ADR-0040 (persistent state is
  overridable), ADR-0016 (nothing secret in a log), ADR-0043/0044 (migrations 0006/0007 rewrite live rows),
  ADR-0046 (daily FX fetch), session 04's audit

## Context

The balance-model work (ADR-0043/0044) ships two migrations that rewrite rows in a live household ledger.
Its first deploy procedure was a script: run `scripts/preview_balance_migrations.sql` against the database
before upgrading, read what it printed, then run validation queries afterwards.

That procedure assumed things an install does not have:

- **An install has no codebase.** ADR-0039's deployment is one compose file and two published images. An
  operator — or an agent acting for one — upgrades with `docker compose pull && docker compose up -d`, and
  often never fetches a newer compose file. A step that needs a script from the repository does not happen.
- **A step a person has to remember gets skipped**, and its output lands wherever they ran it.
- **The container log is not a private place.** Stdout goes wherever the host sends container logs: a
  terminal, `journald`, a log shipper. A validation query that prints account names and balances turns a
  household's finances into log lines.

## Decision

**An upgrade is `docker compose pull` and `docker compose up -d`, and nothing else is required.** Everything
it needs happens in the images, on start, in this order:

1. **`backup` dumps the database** (new compose service, `postgres:16`). `pg_dump -Fc` of the whole database
   into the backups location (`db_backups`, or `METALMARK_BACKUP_DIR` — ADR-0040's pattern), written to a
   `.partial` name and renamed, mode 600, newest ten kept. `migrate` depends on it completing, so **if the
   dump fails, nothing migrates**. It logs the file name and size, never a row.
2. **`migrate` runs `alembic upgrade head`** (unchanged). Every migration that rewrites rows is written for a
   populated database: it copies the rows it changes into the `migration_backup` schema first, out of the app
   role's reach, and its downgrade restores exactly those rows where they have not changed since.
3. **The worker, on start, brings derived state up to date**: the FX fetch first when it is on (ADR-0046),
   then every household's cached base amounts. A check that read a stale rate table would report the
   upgrade's own lag as a finding.
4. **The worker runs the data checks** (`app/services/checks.py`) once per household, then keeps running.
   Each check is a fact the reports rely on: the Accounts total equals today's point on the chart, synced
   investments are valued, synced cards and loans hold debt as negative, hand-entered ones are not in credit,
   every currency has a rate, and synced accounts are still reported. Plus an info line with the balance
   model's current state.
5. **The results are read in Admin → Data checks** (`GET /checks`), live on each load, with the accounts
   behind each finding named — behind the household's own session and RLS.

**The worker's log carries shape only.** For the checks, that means the schema revision (`checks.schema`),
and per household `checks.completed` with each check's status and count, at `warning` when any check warns
or fails. **When something crashes, the log gets the exception's type and the file and line it was raised
at** (`app.worker.failure`), never its message: a database error's message carries the failed statement's
parameters, which are account names, payees and amounts. This applies to every exception the worker logs,
including a crashed sync job, whose full, credential-sanitized message is still stored on its run row, where
Admin shows it.

**Checks are findings, not gates.** A failing check does not stop the worker, fail a healthcheck or block
sync: a restart loop over one bad account helps nobody, and the person who can fix it reads Admin. A check
that crashes becomes a `fail` result naming the exception type. It runs in its own savepoint, so the other
checks still run.

**The way back needs only images.** Downgrade the schema with the *new* image, because only it has the
migrations' downgrades, then pin the old image tag and `up -d`:

    docker compose run --rm migrate alembic downgrade <revision>
    METALMARK_API_IMAGE=…:sha-<old> METALMARK_WEB_IMAGE=…:sha-<old> docker compose up -d

When that is not enough, restore a `backup` dump (the command is in the compose file, beside the service).

## What an old compose file gets

A deployment still on the previous compose file has no `backup` service and does not pass the variables
added since. It still gets steps 2–5: `migrate` and the worker are in the image. The migrations' own row
backups are its way back. **Nothing may depend on the compose file being current.** Every new setting
defaults, in the image, to the value an install without the variable should get.

## Consequences

- **Positive:**
  - Upgrading is the same two commands every time.
  - Every deploy is verified the moment it lands, and verified again on every restart.
  - An agent deploying on someone's behalf has a structured, PII-free signal to read: the `checks.completed`
    log line.
  - A person has the named detail in Admin.
  - The preview script and its manual validation queries are gone.
- **Negative / costs:**
  - **Checks run after migrations, not before.** Nothing previews a migration's effect on a particular
    household. That is traded for backups at two levels (the rows, and the whole database) and for
    downgrades tested against seeded data. A migration that could not be undone this way would need its
    own ADR.
  - **A dump on every `up`.** For a household ledger it is small and quick. Ten are kept on the same disk as
    the database unless `METALMARK_BACKUP_DIR` moves them, so they guard against a bad migration, not
    against losing the disk.
  - **A full disk now stops a deploy**, at `backup`, before anything changes. That is the intended failure.
  - **The worker logs less when it crashes.** Debugging starts from a type and a line, not a message. The
    message still exists where it belongs: on the sync run row.
  - **The checks are live queries.** Each Admin load runs them. That is cheap at household scale; revisit if
    a check becomes expensive.
