# ADR 0040: Persistent state is a named volume by default, and a host path when you say so

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0039 (the standalone deployment this extends), ADR-0036 (backup vs. export),
  ADR-0038 (the overlay this replaced), `deploy/docker-compose.yaml`, `docs/ARCHITECTURE.md` §"Backups"

## Context

ADR-0039 made the deployment a single file that generates its own credentials and applies its own schema,
and put all of its persistent state in Docker **named volumes** — `db_data`, `metalmark_secrets`,
`caddy_data`, `caddy_config`. For the install that decision was aimed at, that is exactly right: nothing to
configure, nothing to get wrong, and `docker compose down -v` is a complete reset.

It is also the one part of this app an operator cannot reach. A named volume lives inside Docker's own
storage area, at a path that differs by platform and by install, and it is not on any filesystem the
operator's existing tooling walks. On a machine whose backups are ZFS snapshots, or a NAS, or restic over a
directory, the ledger is simply not in them. What remains is `pg_dump` — which covers the database and
*not* the Fernet key in `metalmark_secrets`, the split ADR-0039's own consequences already name as the
dangerous one.

So the deployment had one supported shape: correct, and incompatible with being backed up properly by
anyone who knows what they are doing. The request that produced this ADR was to make the location
overridable and leave that default alone.

A second, smaller thing is recorded here. ADR-0039 decision 1 and the README both said the install had "no
`.env` to write" — and `README.md` then wrote one eight lines later, because `METALMARK_SITE` is a decision
only the installer can make. Neither statement was exactly false, since every value has a default and no
`.env` is *required*; both were misleading in the same direction.

## Decision

**We will make each persistent location one variable whose default is the named volume it is today and
whose override is an absolute host path, and we will leave the default install exactly as it is. We will
also state the install's one `.env` line rather than claiming it has none.**

1. **Three variables, one shape.** `METALMARK_DB_DIR` (`/var/lib/postgresql/data`),
   `METALMARK_SECRETS_DIR` (`/secrets`) and `METALMARK_CADDY_DIR` (`/data`), each written at its mount site
   as `${VAR:-default_volume_name}`. Compose decides the source by the *shape* of the value, which is what
   lets one expression serve both cases: a leading `/` is a bind mount, and a bare name that matches a
   top-level `volumes:` entry is that named volume. Measured, not assumed: unset, the resolved
   configuration is identical to what it was before this ADR.

   `caddy_config` is deliberately not overridable. Caddy regenerates it from the Caddyfile that lives inside
   the compose file, so there is nothing in it worth an operator's backup tooling.

2. **The container side stays literal.** `/secrets`, `/var/lib/postgresql/data` and `/data` are constants,
   never read from a variable, so no process can observe which host directory backs it. That is what keeps
   this change to the deployment's *storage* rather than to anything the app does — and it is the reason
   `secret-init`'s `METALMARK_SECRETS_DIR: /secrets` must never be "tidied" into `${METALMARK_SECRETS_DIR}`.
   They are different namespaces with the same name, and collapsing them would send `bootstrap_secrets` to
   write inside the container while the api reads an empty volume.

3. **The four names stay declared under `volumes:`.** Not tidiness: the unset branch of `${VAR:-…}` has to
   resolve to a declared volume, and an undeclared name is a hard error (`service "db" refers to undefined
   volume`). When a variable is set, the volume it names is pruned from the project and never created.

4. **The `.env` is stated rather than denied.** The README presents creating it as a step and says, next to
   the command, that the name in `METALMARK_SITE` is the address you must browse to. The claim that
   survives is the one ADR-0039 actually bought: no credential to write down, because the deployment
   generates its own.

## Consequences

- **Positive:** an operator whose machine already has backup tooling can put the ledger inside it. That is a
  strictly stronger position than `pg_dump`, which against a *running* Postgres is the weaker guarantee and
  was previously the only one on offer.

- **Positive:** the default did not move. Every variable unset resolves to the same named volumes, the same
  project, the same `down -v` reset. The change is opt-in per location, and undoing it is deleting a line
  from `.env` — which is what makes it safe to document.

- **Negative / costs — the database directory is re-owned, recursively and destructively.** `postgres:16`'s
  entrypoint runs `chmod 00700 "$PGDATA"` and `find "$PGDATA" \! -user postgres -exec chown postgres '{}' +`
  before `gosu`. Measured end to end on this deployment with all three variables pointed at host
  directories: inside the container `/var/lib/postgresql/data` is `drwx------ postgres postgres`, and a
  full cluster (`PG_VERSION` 16, `base/`, `pg_wal/`) is on the host. The directory must be **dedicated and
  empty** — anything else in it is re-owned too — and the corollary is that the operator's own account can
  no longer read it. "Your backup tooling can reach it" is true of root-run and snapshot tooling, not of a
  per-user `rsync`.

- **Negative / costs — a new failure that reads as a different one.** uid 999 must be able to *traverse*
  the secrets directory and every parent. `mkdir -m 700` on a secrets directory — a natural instinct —
  leaves Postgres unable to read `postgres_password`, and it reports an authentication failure that names
  neither the directory nor the permission. Mode `0755` is fine; the reader needs `o+x`.

- **Negative / costs:** `down -v` stops being a reset. Measured: with a path override, the volume is pruned
  from the resolved project, so `-v` neither removes the host directory nor a `db_data` volume left behind
  by an earlier un-overridden install — that one becomes an orphan. The equivalent reset is deleting the
  directories, which takes a container because of the ownership above.

- **Negative / costs — the silent half of migrating.** Pointing `METALMARK_DB_DIR` at an empty directory on
  a deployment that already has data does not move that data. `initdb` runs, the schema is applied, open
  signup admits the first person, and the stack comes up *healthy and empty* while the old ledger sits
  unreferenced. The other direction — a new secrets directory against the old database — fails loudly with
  an authentication error. That asymmetry is why the first one gets a recipe in the README rather than a
  warning.

- **Negative / costs:** Postgres documents network filesystems as unsafe for its data directory. Pointing
  `METALMARK_DB_DIR` at a NAS share is the most natural reading of "put it where my backups are" and is the
  one target that is not supported: file locking and durable `fsync` are not reliable over SMB or NFS, and
  root-squash breaks the entrypoint's `chown` as well. The supported shape is local disk that snapshot
  tooling already covers. The other two locations are far more forgiving — small files, written once —
  though root-squash still breaks `secret-init`.

- **Follow-ups:** `scripts/verify.sh`'s `prod` gate boots the deployment a second time with all three
  variables pointed at host paths, and asserts that the mounts resolve as binds with `read_only` intact,
  that the credentials and `PG_VERSION` land on the host, and that a `down`/`up` with no `-v` preserves the
  cluster and the login. The claims above are measured by that gate, or they are not claims.

- **Follow-ups:** that gate must `unset` the three variables before its default half. `verify.sh` inherits
  the caller's environment and the deployment also reads `deploy/.env`; without the `unset`, a developer who
  has one of these exported silently stops testing the default this ADR exists to preserve.

- **Follow-ups:** ADR-0039 is annotated rather than edited, per the immutability rule. When the override is
  used, two of its sentences stop being true — decision 2's "deleting the volume is the only way to get a
  new one" (now the volume *or* the directory) and consequence 2's "there are **two** volumes" (now two
  volumes, or the directories you named and however many you moved) — and its "no `.env` to write" is the
  misleading one above.
