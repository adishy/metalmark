# Architecture Decision Records

We record every architecturally significant decision as an ADR (Michael Nygard format). This is the
authoritative log of *why* MetalMark is built the way it is — agents and humans read it before changing a
load-bearing choice.

## Rules

- **One decision per file**, named `NNNN-kebab-title.md` (zero-padded, monotonically increasing).
- **ADRs are immutable once `Accepted`.** You don't edit the decision later — you add a *new* ADR that
  **supersedes** it, and set the old one's status to `Superseded by ADR-XXXX`. This preserves the reasoning
  trail.
- **Statuses:** `Proposed` → `Accepted` → (`Superseded by ADR-XXXX` | `Deprecated`). Use `Proposed` for a
  decision under discussion; only `Accepted` decisions are binding.
- **When to write one:** anything that changes the data model, a cross-workstream contract, a technology
  choice, security posture, or a correctness invariant. If a code review would ask "why is it done this way?",
  it needs an ADR.
- **Keep it short** — context, the decision, and the consequences (including the costs). Link related ADRs and
  the relevant `ARCHITECTURE.md` section. Copy `0000-template.md` to start.
- **Process:** a new ADR lands in the same PR as the change it justifies (or ahead of it as `Proposed`).
  Changing an `Accepted` decision without a superseding ADR is a review-blocking error.

## A note on the old codename

ADRs ≤0025 (and everything under `agent_record/`) describe the app by its original working codename,
**Kestrel**. It was renamed to **MetalMark** — code, infra, env vars, cookie, database and docs — in one
behavior-neutral commit; these accepted records were deliberately left as written, per the immutability
rule above. When an older ADR says `KESTREL_SECRET_KEY`, read `METALMARK_SECRET_KEY`, and so on.

## A note on the named comparison

Several ADRs ≤0016 justify a decision by naming the commercial product this app was measured against —
"the owner's #1 complaint is X", "this closes its biggest gap". Those records were edited in a later
behavior-neutral pass to describe the category instead ("the hosted apps", "the mainstream
personal-finance apps"), because the app should describe itself on its own terms rather than as a version
of somebody else's.

This is an edit to `Accepted` records, which the immutability rule above forbids, so it is recorded here
rather than left silent — the rule exists so that a reader can see *why* a decision was made, and a
silent edit is what would actually break that. What changed is the brand name only, and the line wrapping
it forced: every threshold, alternative and cost in those sections is as written, and no decision
changed. The de-branding was also applied to the public-facing surfaces that carry a description — the
`README`, the API description in `backend/app/main.py` (and the `contracts/openapi.yaml` generated from
it), and `docs/PLAN.md` / `docs/ARCHITECTURE.md`.

`agent_record/` is **not** covered by this. It holds verbatim quotes from the owner and real filesystem
paths (`…/tmp/monarch_clone`), so the references there are left as-is: rewriting a quotation would make
the record lie about what was said.

## A note on the default ports

The deployment first published its two doors on `8080` and `8443` — the conventional "alternative
http/https" pair, which is exactly what makes them contended: on a machine already running other
self-hosted services, the very first `up` failed on a port that had nothing to do with this app. The
defaults are now **`8791`** (the loopback http door) and **`8790`** (Caddy's), deliberately un-round for
that reason. Decision 5 of ADR-0038 states them and was updated with them; the measured-evidence
paragraph in that same decision still names `8443`, because that is the port the measurement was actually
taken on, not the one in force now. So, as with the codename note above: **where an accepted record names
`8080` or `8443` for a published door, read `8791` or `8790`.**

Neither number is load-bearing. Both come from `METALMARK_HTTP_PORT` / `METALMARK_HTTPS_PORT`, and
`scripts/verify.sh` reads those same two variables — so the `prod` gate probes whatever a deployment was
actually told to open, rather than a default it did not use.

The *interface* the http door opens on has since stopped being loopback-only, so "the loopback http door"
above names what it was: `deploy/docker-compose.yaml` now publishes it on `0.0.0.0`, overridable with
`METALMARK_HTTP_BIND`. ADR-0041 records that decision and its one real cost — the LAN can reach an origin
on which the `Secure` session cookie will not let anyone log in — and is the place to read before assuming
the door is local.

## A note on the deployment file's name

The deployment has been one file under three names, and two accepted records name one of the older ones:

| Name | Named by | What it was |
|---|---|---|
| `docker-compose.prod.yml` | ADR-0038 | an overlay, run with a second `-f` against the dev stack — deleted |
| `deploy/compose.yaml` | ADR-0039 | the standalone file, fetched on its own — the install |
| `deploy/docker-compose.yaml` | — | the same file, renamed |

**So where an accepted record names an earlier name, read the last one.** Both records are left as written,
per the immutability rule above: ADR-0039's body says `deploy/compose.yaml` throughout, and ADR-0038's
status line says the overlay "is `deploy/compose.yaml` now".

The rename is a `git mv`: a move, not a new file, so the history behind the old path follows it. Other
changes to that file landed in the same commit — they are edits to the deployment, not part of the rename.
Compose discovers
`compose.yaml`, `compose.yml`, `docker-compose.yaml` and `docker-compose.yml` on its own, and when it finds
more than one it *warns and takes `compose.yaml`* — measured, in a directory holding all four. That matters
in exactly one case: an install directory from before this rename, where a re-fetch puts
`docker-compose.yaml` beside the `compose.yaml` it already had and the old file is the one that keeps
running. The README says so beside the updating command, because that is where someone will meet it. The
current spelling is the one the wider self-hosting tooling looks for.

## Index

| ADR | Title | Status |
|---|---|---|
| [0000](0000-template.md) | Template | — |
| [0001](0001-simplefin-sole-aggregator.md) | SimpleFIN as the sole aggregation provider, behind a pluggable interface | Accepted |
| [0002](0002-poll-based-lan-only.md) | Poll-based sync over LAN/VPN; no webhooks, no public ingress | Accepted (cadence refined by 0028) |
| [0003](0003-boring-stack.md) | Boring stack: FastAPI + Postgres + React/TS + ECharts | Accepted |
| [0004](0004-postgres-job-queue-no-redis.md) | Postgres-backed job queue; no Redis | Superseded by 0029 (claim mechanism half) |
| [0005](0005-money-decimal-never-float.md) | Money as NUMERIC/Decimal; never float | Accepted |
| [0006](0006-multi-currency-dated-fx.md) | Multi-currency is first-class, converted at dated FX rates | Superseded by 0017 |
| [0007](0007-field-provenance.md) | Field-level provenance governs sync-vs-human writes | Accepted (field list widened by 0028) |
| [0008](0008-transfers-linked-excluded.md) | Transfers are linked groups, excluded from cash-flow | Accepted |
| [0009](0009-ledger-decoupled-from-connections.md) | The ledger is decoupled from connections | Accepted |
| [0010](0010-manual-first-and-parity.md) | Manual-first build order and manual-parity principle | Accepted |
| [0011](0011-first-class-investments.md) | First-class investments + consolidated allocation view | Accepted |
| [0012](0012-sharing-view-filtering.md) | Household + view-filtering sharing (no per-account access control in v1) | Superseded by 0026 (ownership half) |
| [0013](0013-auth-sessions-invite-only.md) | Auth: server-side sessions, argon2id, invite-only | Superseded by 0027 (invite half) |
| [0014](0014-tenant-isolation-scoping-layer.md) | Tenant isolation via one mandatory scoping layer / RLS | Accepted |
| [0015](0015-test-harness-first-class.md) | Test harness is a first-class workstream (red-green) | Accepted |
| [0016](0016-admin-sync-observability.md) | Admin-grade sync observability | Accepted |
| [0017](0017-currency-single-account-model.md) | Currency & FX: single-currency accounts, base_amount cache, revaluation line | Accepted |
| [0018](0018-cross-currency-transfers.md) | Cross-currency transfers: match on base amount, surface FX cost | Accepted |
| [0019](0019-provenance-manual-origin-boundary.md) | Provenance: the manual-origin boundary for sync merges | Accepted |
| [0020](0020-investment-cost-basis-average.md) | Investment cost basis: average cost for v1; lots deferred | Accepted |
| [0021](0021-investment-balance-source.md) | Investment balance source: derived vs stated + reconciling plug | Accepted |
| [0022](0022-derisk-sync-p0-spike.md) | De-risk sync early: P0 SimpleFIN spike + Phase-1 vertical slice | Accepted |
| [0023](0023-phase1-split-1a-1b.md) | Split Phase 1 into 1a/1b; carve FX into its own module | Accepted |
| [0024](0024-uv-for-python-deps.md) | uv for Python dependency management | Accepted |
| [0025](0025-rls-implementation-mechanism.md) | RLS mechanism: per-transaction GUC, fail-closed, child policies | Accepted |
| [0026](0026-owners-are-household-data.md) | Owners are household data, not users (ownership half of 0012) | Accepted |
| [0027](0027-open-signup-replaces-invite-only.md) | Open signup replaces invite-only (invite half of 0013) | Accepted |
| [0028](0028-provider-seam-and-cadence.md) | The aggregator seam, per-connection provider selection, cadence bounds, error taxonomy | Accepted |
| [0029](0029-job-claim-heartbeat-fence.md) | Job claim: heartbeat, fence, per-household enumeration (claim half of 0004) | Accepted |
| [0030](0030-ofx-import-scope.md) | OFX/QFX import: 2.x only, FITID-first dedupe, banking rows only | Accepted |
| [0031](0031-auto-split-rules.md) | Auto-split rules: balanced by construction, a human's split is final | Accepted |
| [0032](0032-reconciliation-with-investments.md) | Investments in the reconciliation identity: a third term, and one residual only | Accepted |
| [0033](0033-investment-events-and-the-cash-ledger.md) | Investment events and the cash ledger: a buy is not a transaction | Accepted |
| [0034](0034-position-from-history-when-history-exists.md) | A position's quantity comes from history too, when history exists | Accepted |
| [0035](0035-reports-read-once.md) | A report reads each thing once, and rounds where it says it rounds | Accepted |
| [0036](0036-portable-export.md) | A portable export is a document, a backup is the instance, and no id crosses between | Accepted |
| [0037](0037-desktop-notifications-in-app.md) | Desktop notifications: in-app, not Web Push; the decision to notify is recorded server-side | Accepted |
| [0038](0038-the-deployment-is-an-overlay.md) | The deployment is an overlay: a built frontend, a stated environment, and TLS | Accepted, superseded in part by 0039 and 0041 |
| [0039](0039-the-deployment-is-standalone.md) | The deployment is a standalone file that carries no credentials, and the image is published | Accepted, extended by 0040 |
| [0040](0040-persistent-state-is-overridable.md) | Persistent state is a named volume by default, and a host path when you say so | Accepted |
| [0041](0041-the-plain-http-door-answers-on-the-lan.md) | The plain-HTTP door answers on the LAN, not only on loopback | Accepted |
