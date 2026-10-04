# Overall codebase review — 2026-10-04

User request: “could you do an overall review for the codebase and note any quick improvements / overall design comments and persist in the agent_record”

## Scope and evidence

Reviewed the current working tree, based on `c31392c` (`Agents may name the accounts they create`), including the uncommitted changes present during this review. This is a sampled architectural/code review, not an exhaustive audit of every file or a release certification. The working tree is being edited concurrently; references describe the code read during this session. No application code, migrations, deployment settings, or existing session records were changed by this review.

Read the repository guidance and sampled architecture/ADRs, authentication and household scoping, worker/job handling, investment/report calculations, portability, agent adapters, React query/auth handling, route composition, test fixtures, CI, and deployment scripts. Recommendations below distinguish directly observed behavior from design follow-ups that need measurement or a separate decision.

Verification performed:

- `METALMARK_ENV=test /tmp/mm-backend-check-env/bin/python -m pytest -q -p no:cacheprovider --confcutdir=tests/unit tests/unit/test_money.py tests/unit/test_periods.py tests/unit/test_agent_account_schema.py`, from `backend`: **30 passed**. The existing temporary Python environment was used; this was not a fresh locked dependency installation. Hypothesis used an in-memory example database because its normal location was unavailable.
- `node scripts/design-lint.mjs`, from `frontend`: **passed**.
- Read the relevant mutation/query keys, CI commands, and fixture dependencies directly. No new tests that merely duplicate implementation were added.
- Full Postgres integration, production, browser, and restore gates were not rerun. Docker socket access was unavailable in the preceding review, and this documentation-only task did not require rebuilding the stack. No live household data was accessed. No dependency vulnerability scan or external security assessment was performed.

## Overall assessment

The core architecture is appropriate for a self-hosted household finance app. Its strongest quality is that financial rules are explicit: signed balances, Decimal arithmetic, dated FX, provenance, and the difference between a bank-stated balance and a value derived from holdings. Keep these rules centralized as features expand.

The main maintenance risk is duplication at the edges: query invalidation lists, API-shaped types, transport adapters, and documents describing current behavior. Several large modules also combine orchestration, calculations, presentation, and persistence. Small changes at these boundaries will provide more value than a platform rewrite.

Specific strengths worth preserving:

- **Household isolation is structural.** `backend/app/db.py` establishes transaction-local scope; `backend/app/deps.py` resolves identity before setting it. Metadata-driven RLS coverage tests and worker role checks help catch missing policies and accidental privilege changes. Continue testing reference ownership as well as row visibility: an RLS-protected row does not itself guarantee that every referenced ID belongs to the same household.
- **Financial authority is documented.** ADRs 0005, 0007, 0017, 0019, 0021, 0034, 0035, and 0043 explain which writer or calculation owns each fact. Missing prices/rates remain distinguishable from zero. These are useful design constraints, not incidental implementation details.
- **The job queue has the right failure semantics.** ADR-0029 and `services/jobs.py` describe heartbeat/reaping and claim fencing, not merely a “running” flag. Postgres remains a sensible queue for this workload.
- **Recovery is exercised.** Live-data migration cases, RLS tests, the restore drill, and production-shaped probes cover failure modes that unit tests cannot. Keep these gates even while making the fast test loop cheaper.
- **The UI has shared foundations.** Design tokens, form controls, query error states, date components, chart interaction helpers, and mobile checks reduce the number of places each accessibility or layout fix must reach.

## Quick improvements, in suggested order

### Q1. Complete and centralize query invalidation

**Observed correctness gap; high value; small-to-medium change.**

Evidence: [`frontend/src/api/hooks.ts`](../frontend/src/api/hooks.ts), especially `useCashFlowSankey` and `useInvalidateLedger`; [`frontend/src/api/portability.ts`](../frontend/src/api/portability.ts), `useImportDocument`; [`frontend/src/components/AccountHoldings.tsx`](../frontend/src/components/AccountHoldings.tsx), its local `invalidate` function.

`useInvalidateLedger` invalidates `report-cash-flow`, but the Sankey uses the separate first key element `report-cash-flow-sankey`. Query-key matching does not treat these different strings as the same prefix. An already-mounted Sankey can therefore retain its previous result after a ledger mutation. Holding edits use another list that omits historical report keys even though changing a holding/value can change those reports.

Whole-household import calls only the ledger invalidator, although it can also introduce owners, categories, tags, rules, rates, balances, income, recurring data, and documents. Settings mounts some of these queries outside the selected section, so not all stale data will be rescued by tab navigation. Window-focus refetch is disabled globally.

Create shared key factories and explicit invalidators for ledger changes, investment changes, taxonomy changes, and whole-household imports. Reuse them in component mutations. For a rare whole-household import, invalidating all household queries may be clearer than maintaining another exhaustive list. Include invalidation/refetch completion in the mutation promise where the UI promises refreshed results on success.

Verify with behavior tests: keep a Sankey mounted while editing a transaction; import a new category/owner while Settings is mounted; change a holding while a report observer remains subscribed. Assert the displayed result refreshes without navigation or a reload.

### Q2. Give session transitions an explicit cache lifecycle

**Observed lifecycle gap; small-to-medium change.**

Evidence: [`frontend/src/auth/AuthContext.tsx`](../frontend/src/auth/AuthContext.tsx), `login`, `logout`, and the bootstrap effect; [`frontend/src/main.tsx`](../frontend/src/main.tsx); [`frontend/src/api/client.ts`](../frontend/src/api/client.ts).

The QueryClient lives above AuthProvider and survives logout. Logout clears `me` and the CSRF token but does not cancel or clear queries. A subsequent sign-in can reuse cached responses from the prior session. Separately, an ordinary API 401 throws `ApiError` without updating auth state, so expiration during use can leave the signed-in shell showing failures until the session probe runs again or the app reloads.

Cancel in-flight household reads and clear session-dependent queries on logout or principal changes. Add an explicit session-expired event/callback for protected-request 401s, keeping network/5xx failures distinct from expiry and keeping a failed login from triggering a redirect loop. Thread an AbortSignal through the fetch wrapper so cancellation can also stop the request.

This is a cache hygiene and recovery recommendation, not a demonstrated cross-household server authorization bypass. The installation intentionally has one household. Test logout/sign-in, expiration mid-session, and a slow old response arriving after logout.

### Q3. Make the local frontend gate match CI

**Directly observed tooling mismatch; very small change.**

Evidence: [`scripts/verify.sh`](../scripts/verify.sh), `gate_frontend` around line 240; [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), frontend job; [`frontend/AGENTS.md`](../frontend/AGENTS.md).

CI runs `npm run lint:design`; the local frontend gate runs typecheck, tests, and build but omits design lint. This contradicts the stated “every CI gate locally” workflow and lets a local green run fail the design gate later.

Add design lint to the local gate in the documented order. Prefer one shared package script for that sequence to reduce future drift. The current design lint passes; the issue is gate coverage, not a reported design violation.

### Q4. Let pure unit tests run without Postgres

**Directly observed test-harness coupling; small-to-medium change.**

Evidence: [`backend/tests/conftest.py`](../backend/tests/conftest.py), session-autouse `_configure` around line 86, and its dependent autouse fixtures.

The parent conftest starts/recreates Postgres and runs migrations for ordinary test collection, including pure unit tests. Marking tests `unit` does not remove an autouse fixture. The focused pure tests above passed by explicitly cutting off that parent conftest, which should not be the normal command a contributor must discover.

Move database setup behind an explicit fixture requested by integration tests, or move it into `tests/integration/conftest.py`. Keep lightweight environment defaults available to unit tests and audit the other autouse fixtures before moving anything. Provide separate fast-unit and full-integration commands. Preserve the existing guarded scratch-database naming and real-Postgres/RLS coverage.

### Q5. Move password hashing off the async request loop

**Observed blocking call path; performance improvement, with no load benchmark performed.**

Evidence: [`backend/app/services/auth.py`](../backend/app/services/auth.py), `signup` and `login` around lines 50 and 121; [`backend/app/security/passwords.py`](../backend/app/security/passwords.py).

The async auth services call synchronous Argon2 hashing/verification directly. That blocks the event-loop thread for the operation, potentially delaying unrelated requests on the same worker. Offload the KDF work through the existing threadpool pattern, with a sensible concurrency bound.

The login comment also says “Constant-ish work whether or not the user exists,” but `user is None or ...` skips verification entirely for an unknown email. Either perform verification against a fixed dummy hash for that branch or correct the claim; implementing the dummy path is preferable if limiting account-existence timing signals is intended. Open signup intentionally reveals some identity information already, so this should not be presented as a complete enumeration defense.

Verify scheduler responsiveness during concurrent login attempts and exercise known/unknown users without relying on brittle exact wall-clock assertions. Rehash-on-success can be considered alongside this; `needs_rehash` already exists.

## Broader design follow-ups

### D1. Keep route adapters above shared application operations

Evidence: [`backend/app/api/agent.py`](../backend/app/api/agent.py) imports the browser transaction route and private investment route helpers (`_holding_out`, `_record_for`).

Sharing behavior is correct, but importing route functions makes transport details part of the reusable boundary. Extract a small shared application operation/presenter when these paths next change, leaving browser and agent routes responsible for authentication, schema validation, response handling, and anonymization. Preserve the same transaction and RLS context through that operation. Avoid turning this into a generic repository or service framework.

Separately, the browser types in `frontend/src/api/types.ts` are maintained by hand despite a checked OpenAPI contract. Consider generating wire types from the contract, with domain-specific aliases/wrappers for money strings and presentation types. OpenAPI drift checks compare the backend to the contract; they do not prove that handwritten frontend types agree. For Python, start typing the shared write boundaries/protocols before introducing a repository-wide mypy gate.

### D2. Split frontend features at existing boundaries and load routes on demand

Evidence: [`frontend/src/pages/Settings.tsx`](../frontend/src/pages/Settings.tsx) was 2,211 lines and already contains named sections for connections, categories, currencies, household, data, owners, and rules. Admin was 1,151 lines. [`frontend/src/App.tsx`](../frontend/src/App.tsx) imports all route components eagerly, including the design-system page.

Extract one Settings section at a time into a feature module, retaining its tests and shared controls. Apply the same approach to Admin's connection, run, check, and token panels. Use lazy route imports with an appropriate loading/error boundary. Measure initial bundle/load changes before promising a speedup; this review did not measure bundle size. File length is an indicator of mixed responsibilities, not a reason to split arbitrary line ranges.

### D3. Extend batched report reads to portfolio valuation

Evidence: [`backend/app/services/investments.py`](../backend/app/services/investments.py), `value_portfolio`, `value_account`, `_value_holdings`, and `_convert_ccy`; [`backend/app/services/fx.py`](../backend/app/services/fx.py), `Converter`; ADR-0035.

Portfolio valuation loops over accounts; holding valuation performs two FX conversions per position through the database-backed multiplier path. Same-currency conversions short-circuit, but repeated foreign-currency positions repeat lookups. The existing batched report/FX design provides a good foundation for reducing this.

First measure query counts on a representative household with multiple accounts, foreign securities, trades, and a long history. Then preload positions/prices/rates once per valuation request. The current Converter exposes conversion into base currency; extend it carefully for arbitrary account-currency conversion and triangulation rather than assuming its current interface covers both hops. Preserve existing Decimal rounding and missing-rate semantics with equivalence tests and a query-count budget.

### D4. Define the consistency boundary of an export

Evidence: [`backend/app/services/portability.py`](../backend/app/services/portability.py), `export_document` and `write_archive`; [`backend/app/db.py`](../backend/app/db.py); ADRs 0036 and 0060.

Exports read many tables and archive content in separate statements. The engine/session setup does not explicitly request a repeatable-read snapshot. Under the normal PostgreSQL read-committed isolation level, a sync or document deletion can commit between those reads. In particular, an archive manifest and its later content enumeration can describe different document sets. This is a code-backed concurrency concern, not a reproduced production incident.

Choose and test a coherent snapshot for export generation, preferably with a dedicated read transaction after authentication. Do not globally change isolation on every request: auth resolves/touches sessions, and long-running snapshot transactions have costs. Add a concurrency test that changes/deletes a document during export and checks that every manifest entry has matching bytes. Preserve the distinction between a portable export and an instance backup.

As attachments grow, also measure temporary disk, transaction duration, and browser download behavior. The server builds the archive in a temporary file, while `frontend/src/api/portability.ts` waits for a complete Blob before initiating the browser download. The code therefore still has whole-archive resource costs even though server memory is bounded per attachment. ADR-0060's database storage remains a reasonable choice until measurements justify a different one.

### D5. Treat portable identity as a product rule, not only an importer detail

Evidence: `_import_accounts` in [`backend/app/services/portability.py`](../backend/app/services/portability.py), `create_account` in [`backend/app/services/ledger.py`](../backend/app/services/ledger.py), and ADR-0036.

Manual account import identity is normalized name + type + currency. Browser account creation does not impose that uniqueness, while the in-progress agent path now explicitly chooses a distinct name. This leaves a broader mismatch: two legitimate manually created accounts with the same identity tuple can collapse on restore, irrespective of how the agent path behaves.

Decide whether same-name accounts are supported. If they are, preserve occurrences or introduce explicit portable identity semantics; if they are not, enforce and explain the restriction consistently at all creation/rename boundaries. Do not simply reuse source primary keys: ADR-0036 explains why they cannot be copied across households. Add round-trip tests with duplicate names, renames, owners, documents, and holdings. A change to accepted import identity needs a superseding ADR and populated-data compatibility planning.

### D6. Add a small, privacy-conscious operational signal

Evidence: [`backend/app/logging.py`](../backend/app/logging.py) has structured logging and intentionally suppresses credential-bearing library request logs. [`backend/app/main.py`](../backend/app/main.py) does not add application-level request correlation/timing middleware. Sync already has run IDs and duration fields.

Add a request ID and structured method, route template, status, and duration fields to API completion/error logs. Link those to job/run/token IDs where relevant. Use route templates rather than raw URLs, and never log request bodies, cookies, tokens, financial descriptions, or arbitrary query strings. This offers useful local diagnostics without requiring a new telemetry service. Test redaction alongside the existing logging-leak tests.

### D7. Keep the design record navigable and distinguish decisions from implementation notes

Evidence: [`docs/adr/README.md`](../docs/adr/README.md) has substantial historical naming/deployment notes and did not list ADR-0062 in the index when read. `docs/ARCHITECTURE.md` still describes the initial agent-token scopes even though the implemented scope set is wider. Several large source files also carry long narratives about earlier defects beside current control flow.

Add a lightweight ADR inventory/index check and update the current architecture overview when a new accepted decision changes it. Preserve accepted ADR history; use superseding ADRs for semantic changes. Keep concise invariant and failure-mode comments close to code, and move long implementation history into the relevant ADR or session record when touching that area. The goal is to make the current rule easier to find without deleting the evidence behind it.

## Relationship to the earlier PR #48 review

The earlier review examined `d3d9175`. The current tree contains subsequent work on all three findings: import authentication before manual multipart parsing plus a configurable incoming-body bound; distinct agent account names; and corrected cost-basis currency wording. Those changes were observed, but their full integration validation was not repeated here. They should not be represented as three unchanged open findings against this working tree.

D5 records the remaining general browser/import identity question, rather than claiming the updated agent naming path still has the original defect. The account-name permission itself also changed after the earlier PR head; its privacy tradeoff belongs in the explicit ADR/permission review and should not be silently inferred from the earlier fixed-prefix design.

## Suggested sequence

1. Take Q3 first: close the local/CI gate mismatch with a very small change.
2. Address Q1 and Q2 in focused correctness PRs with mounted-view/session-transition regression tests.
3. Separate pure unit fixtures (Q4), then improve auth scheduling (Q5).
4. Decide portable account identity and export consistency (D5/D4) before promising lossless household moves under all supported inputs and concurrent activity.
5. Refactor feature boundaries and measure valuation/bundle costs as the next related features are edited. Keep the existing stack, RLS, Decimal rules, recovery gates, and centralized financial authority.

Only this new review record was intentionally added by this session. No recommendations were implemented and no GitHub comments were posted for this overall review.
