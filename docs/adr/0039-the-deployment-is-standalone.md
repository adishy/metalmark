# ADR 0039: The deployment is a standalone file that carries no credentials, and the image is published

- **Status:** Accepted, **extended by ADR-0040** — which makes the *location* of the persistent state
  overridable and nothing else. Everything here stands as written for the default install, including "it
  generates its own credentials on first boot" and the named volume they are written into. Two sentences
  here stop being true only when an override is in use, and are named in ADR-0040's follow-ups: decision
  2's "deleting the volume is the only way to get a new key" (the volume, or the directory backing it) and
  consequence 2's "there are **two** volumes". The "no `.env` to write" in decision 1 is a third: no `.env`
  is *required*, because every value has a default, but the README now presents the one line
  `METALMARK_SITE` needs as a step rather than leaving it unsaid.
- **Date:** 2026-09-20
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0038 (the deployment overlay this replaces — its other decisions stand),
  ADR-0037 (the service-worker gap that made a deployment necessary at all), ADR-0002 (LAN/VPN only),
  ADR-0027 (open signup, which is what removes the seed step), `docs/ARCHITECTURE.md` §1, §5

## Context

ADR-0038 made the deployment work, and it did so as an **overlay**: two `-f` files, the same compose
project, the dev stack's `.env`, a Fernet key you create by hand, and a migration you run by hand.

```bash
mkdir -p secrets && openssl rand -base64 32 | tr '+/' '-_' > secrets/metalmark_secret_key
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api alembic upgrade head
```

Every part of that is defensible on its own. Together they are not an install: they are four commands, one
of which cannot be guessed, and two of which exist only because the deployment was layered on a stack
designed for developing the app. The question that produced this ADR was simply *can this be put on
another machine by someone who has not read the repo* — and the answer was no, for a reason that had
nothing to do with any individual step: **the `-f a.yml -f b.yml` form requires the checkout**, so even a
perfect one-line version of it still starts with `git clone`.

That matters because the deployment is not only for the person who wrote it. The comparison that was
actually made is to the self-hosted apps this category is full of — Jellyfin, Immich — where the
instruction is a compose file you curl and a `docker compose up -d`. The repo had no artifact at all:
nothing was published, so there was nothing to fetch even if the file had been standalone.

There was also a credential problem hiding inside the convenience question. A deployment whose secrets
come from a file the operator creates is a deployment whose *documentation* has to describe creating
them, and the natural next step after "here is where the key goes" is "here is a default key". This repo
holds exactly one real bank credential at a time and encrypts it at rest with that Fernet key; a default
in a public repository would be the worst version of that mistake.

## Decision

**The deployment is a single standalone compose file that generates its own credentials and applies its
own schema, and the images it runs are published to GHCR so that installing it requires no checkout.**

1. **One file, `deploy/compose.yaml`, complete on its own.** No second `-f`, no `.env` to write, no
   `secrets/` directory to create. The overlay (`docker-compose.prod.yml`) and the root `Caddyfile` are
   deleted rather than kept in sync: two files describing one deployment is the drift this is avoiding,
   and there is now exactly one place that says what a deployment is.
   Measured, not assumed: `-f deploy/compose.yaml` reads `deploy/.env`, not the repo's `.env`, so a dev
   stack's `METALMARK_ENV=dev` cannot be inherited by the deployment even in principle.

2. **It generates its own credentials on first boot** — `app.bootstrap_secrets`, a one-shot container
   that runs before anything needing a secret. Three files into a named volume: the Fernet key, the
   Postgres owner's password, the app role's password. It **never overwrites an existing file**, because
   regenerating the key would not rotate anything — it would make every stored access URL undecryptable,
   and the failure would surface at the next sync rather than at the command. Deleting the volume is the
   only way to get a new key, which is the right shape for a deliberate act.

3. **It applies its own schema.** A one-shot `migrate` container (`alembic upgrade head`) consumed through
   `service_completed_successfully`, so `docker compose up -d` does not return until the schema is
   current. ADR-0038's rule that migrations are always explicit is unchanged and still true — CI's
   `contract`, `drill`, `e2e` and `prod` jobs each run it as their own step on the dev stack. What changed
   is that a *deployment* states it, which is the difference between an install and an install with a
   footnote.

4. **`Settings` accepts docker's `_FILE` convention for both database credentials.** The Fernet key
   already did; the two passwords follow, so a generated credential has somewhere to go. ARCHITECTURE §5
   already said secrets come from files and never from plain environment variables — this makes that true
   for the database passwords rather than aspirational. Precedence, decided by test: the file wins over
   the variable when both are set, and a file that is set but unreadable or empty leaves the variable
   alone rather than blanking it, so a mount mistake does not present as `password authentication failed`.

5. **The Caddyfile is inlined into the compose file, interpolated by compose.** One expansion, by the tool
   already reading the document, instead of Caddy resolving `{$METALMARK_SITE}` from its own environment
   for a file compose does not interpolate — a split that makes setting the variable look like it worked
   while changing nothing. The site address is `${METALMARK_SITE:-localhost}`, so the same one-file
   install is correct for `localhost` and for a LAN name.

6. **No `name:` field.** The compose project is named after the directory the file is in. For an install
   that is the directory you made, which is what you want; for a checkout it is `deploy`, which means
   running this file by hand cannot land on the dev stack's project and replace its containers. A pinned
   `name:` would have been a footgun for exactly the people most likely to type the command.

7. **CI publishes multi-arch images to GHCR, and then boots *those* through the same gate.** The
   `publish` job builds `linux/amd64` + `linux/arm64` (the machines people self-host on are frequently not
   amd64), pushes `sha-<commit>` and `latest`, and then runs `scripts/verify.sh prod` with
   `PROD_IMAGE_PREFIX`/`PROD_IMAGE_TAG` so the gate **pulls** the published artifact instead of building
   the same source a second time. It `needs:` every verifying job, because `latest` is what a stranger's
   `docker compose up -d` pulls and nothing reaches it unverified.
   With `image:` and `build:` both present, `docker compose up` pulls and does not build (measured: the
   run reports `No services to build`), so a no-clone install never touches the `../backend` context that
   is not there, while `--build` still builds from a checkout.

8. **No credential can reach the published image, and that is a gate rather than an intention.**
   `backend/.dockerignore` excludes `.env` from a build whose Dockerfile ends `COPY . .` and whose output
   is public; `scripts/secret_scan.sh` scans the worktree and every commit on every ref for the shapes a
   credential has, and never prints what it matched — only `path:line` and the rule name, because a
   scanner that echoes a leaked token into a CI log has found the secret and published it. It is both a
   `verify.sh` gate and a CI job, and the third claim — about the *image* — is asserted in the `prod` gate
   by asking a running container, since that is the only place it is answerable.

## Consequences

**The install is now the thing the user asked for.** One file, one command, and then a signup — ADR-0027's
open signup means the first person to register creates the household, so there is nothing to seed either.
That is a shorter path than the dev stack's, which needs a migration and a separate seed.

**Three things it costs — and, on measurement, one of them it does not:**

1. **A one-time manual step in the GitHub UI — which the first publish that reached it did not need.**
   The reasoning is why the guard exists and it stands: GHCR creates packages private by default even in a
   public repository, and a private package breaks `docker compose up -d` on a machine that has never
   authenticated — the one audience with no context to debug it. The publish job therefore asserts
   anonymity (`docker logout`, then pull) and fails with the URL to click if the package is not public.
   This is deliberately a red job rather than a warning: an install path that silently stops working is
   worse than a build that says so.

   **Measured:** both packages were anonymously pullable on the first run that reached the assertion, so
   the cost recorded here was not paid and nobody has to click anything before the install in the README
   works. Verified independently of CI as well, because a `docker pull` on the runner could have succeeded
   from the image cache it had just populated: an anonymous GHCR token fetches `manifests/latest` for both
   repositories with a `200`. The guard stays — one `docker pull` per run buys a loud failure for a silent
   one, and the silent version lands on the person least able to diagnose it.

2. **A bare install has no encrypted backup.** `scripts/backup.sh` does the dump-and-encrypt, and
   `scripts/` is outside the image's build context, so reaching it needs a checkout. The README gives the
   unconditional `pg_dump` one-liner instead and states the part that is easy to miss: there are **two**
   volumes, and a dump without `metalmark_secrets` restores the ledger but not the bank connections,
   because an access URL whose key is gone is a reconnect rather than a restore.

3. **Deleting one volume and not the other is now the interesting failure.** The generated credentials
   and the database are separate volumes on purpose, so a partial deletion leaves a database that cannot
   authenticate rather than a database that is gone. It fails loudly at first boot with an authentication
   error, and the recovery is `docker compose down -v` — which is also the only way to start over.

**What was removed matters as much as what was added.** The `!override` and `!reset` compose tags are gone
from the repo along with the overlay that needed them, and so is the only place a dev-stack `.env` could
be interpolated into a deployment. Two consequences of ADR-0038's shape — the overlay's `ports: !reset []`
and its literal `METALMARK_ENV: prod` — stop being defensive measures and become properties of the file's
structure.

**ADR-0038 is superseded in part.** Its decisions 1 (an overlay) and 6 (how the site address reaches the
Caddyfile) are replaced by decisions 1 and 5 here. The rest — the built frontend and why it is the point,
`METALMARK_ENV=prod` stated literally and what it does, TLS being non-optional, and the `prod` gate — stand
unchanged and are still the reason the deployment exists.
