# Session 11 — UX and account tools

User requested quieter report exceptions, a clearer net-worth breakdown, account holdings overrides, account document management with portable export, agent token copy repair, alignment/search/focus fixes, scoped transaction and holdings agent writes, and a Meltmeter-inspired pastel design. Authorized up to two subagents at once and read-only anonymized production debugging. Production credential omitted from this record.

Work started from main on ux-account-tools. Two agents assigned holdings overrides and account attachments; primary handles shared UX, clipboard, integration and agent writes. Production will not be mutated.

## Progress — first implementation pass

- UX: shared DataNotes native details disclosure replaces expanded history/report warning footnotes in Accounts and Insights. Net-worth residual remains exact and visible as Other balance changes; per-account attribution collapses. Header household callout removed; search widened with aligned shortcut. Focus defaults rounded within CSS base layer so component radius wins. Warm paper light surfaces and warm dark surfaces inspired by sibling Meltmeter; existing icon/navigation system preserved.
- Clipboard: modern API first, selected-field execCommand fallback for insecure LAN contexts, explicit manual-copy status if neither succeeds.
- Holdings agent: ADR-0059, migration 0016, per-account metadata/value overrides, AccountHoldings editor, provider sync protection. File agent: ADR-0060, migration 0017, Postgres byte storage, account attachment CRUD/download/PDF preview, portable export v3 retaining imports v1/v2. Both integrated into account dialog.
- Agent writes: ADR-0061 partially supersedes 0048. Explicit opt-in transactions:write and holdings:write; POST creation only, household owner required; existing holdings refused under account lock. GET/debug grants unchanged and DB read-only. Response anonymization retained. Existing tokens do not gain scopes.
- Branch ux-account-tools. Demo stack mm-dev built and running at localhost:5173; migration retry needed during creation because 0017 briefly appeared before 0016. Never mutated production; no production API reads needed so far.
- Initial frontend typecheck/design lint and 614 tests passed before new account component integration. Agent tests running in metalmark_test_agents; agents use separate scratch DBs. Logs in /tmp/metalmark-agent-tests.log. Next: complete security tests, full gates, contract regeneration, measured design contrast, browser screenshot/e2e review and handoff notes.

## Resume and verification checkpoint

User requested ongoing records; this file is updated append-only. Two continue messages resumed the same task. Backend agent security/anonymization suite passed 171 tests; holdings agent reports 181 focused tests; documents agent reports 4 attachment tests and 39 portability/migration tests. Demo migrations 0016/0017 and seed succeeded. Screenshot sweep completed for all pages in light/dark at 390/1280; primary viewed warm light Accounts and dark phone Insights successfully. Final frontend gate was still running during interruption. New account-management e2e underway with own temporary accounts; does not touch production.

Environment switched approval policy to never on resume. Newly requested Docker full pytest and contract commands denied; cannot request escalation. Contract redirection briefly truncated the file before denied execution; restored from HEAD immediately. Need to regenerate via available permitted tooling, run full gates and inspect account dialog artifacts before marking complete. Credential remains absent from files/logs. Remaining polish: portfolio caveats/currency alignment, agent write security review, update docs/runbook discovery, review diff and final validation results.

## Verification recovery

Recovered contract generation without Docker via installed Python dependencies; API contract regenerated and diff reviewed (attachment routes, scoped POST schemas, holding metadata/override flag, token scope enum). Added holding override names to known-name scrubber to avoid leakage through report prose. Backend Ruff clean and git diff --check clean.

Local frontend typecheck/design lint and all 619 tests passed. Production build passed from clean /tmp source copy (original ignored dist had container ownership, preventing sw.js writes). Copied cached Python dependencies to a writable /tmp environment offline; 547 pure backend unit tests passed using --confcutdir=tests/unit to avoid irrelevant automatic Docker DB setup. Standard full backend suite is now running in demo container, isolated metalmark_test_full. Docker direct commands remain permitted under saved prefix; new commands with shell redirection were the denied ones. No permission bypass or production access occurred.

Temporary frontend node_modules symlink caused cross-container Node resolution and demo Vite failures; restored a full dependency directory rather than a symlink. Browser agent will rerun corrected account-management spec (required-field selectors corrected, teardown timeout separate), clean two zero-balance accounts left by the first failed demo run, and capture account dialog screenshots. Existing screenshot sweep has all 26 pages/theme/width artifacts in /tmp/mm-ux-shots. No production screenshots/data were captured.

## Browser workflow verified

Account-management browser spec passed 3/3: at 360/1280px, add/edit/reopen holding, upload PDF/PPTX, byte-identical downloads, PDF preview headers, keep/delete confirmation, no horizontal modal overflow; disclosure opens only on request. Browser agent cleaned its own test accounts/securities and both earlier empty failed-run accounts through authenticated demo API. Primary viewed 360px documents screenshot: controls align and long filenames wrap. Persistent copies in /tmp/mm-account-review/account-{documents,holdings}-{360,1280}-light.png. Container production build also passed (1101 modules + PWA service worker), only pre-existing chunk-size warning.

Additional UX polish: portfolio valuation caveats collapse under Valuation details; account native currency amount has its own aligned full-size line, and base amounts are text-base. Targeted frontend tests rerunning for this small edit. Broader mobile/chart/command palette checks requested. Full backend suite continues on metalmark_test_full.

## Dated handoff — 2026-10-04

User explicitly requested persisting progress, the main tasks and original prompt with today's date. This checkpoint is a handoff, not a declaration that implementation is complete. Work is uncommitted on branch `ux-account-tools`.

### Original request and constraints (credential redacted)

Take a look at this application and make these UX changes:

1. Reduce large walls of exception text in small footnotes, including “Counted partway through”, missing FX rates, and currency moves that cannot be attributed over a period.
2. Make the net-worth change graph readable when recent account additions make most changes unattributable.
3. Allow per-account manual holdings edits and additions, including holding type, symbol and value, as overrides to provider sync and additional metadata.
4. Upload, view, manage and delete arbitrary PDF, DOC and PPTX account files; correctly persist them in the database and include them in portable exports.
5. Fix the agent API key copy button.
6. Fix alignment, including account currency callouts, upload-image buttons and search. Remove the household-name header callout, lengthen search and align Ctrl/Cmd K with the search label.
7. Add agent API write capabilities limited to transaction entries and adding account holdings.
8. Give focus outlines a border radius.
9. Keep the overall UX, UI and icons while transplanting pastel colors and layout lessons from sibling `../meltmeter`.

Prioritize the work and use smaller subagents, at most two concurrently. Production is at `192.168.0.123:8791`; anonymized agent/debug reads were authorized with utmost respect for real data. The supplied secret is intentionally omitted; never recover it from this record or commit it. No production reads, writes, migrations or deployment have occurred. Later user instructions: record progress as work proceeds, continue, and now persist a dated handoff.

### Current implementation and verification

- Two delegated streams implemented holdings and account files; primary implemented shared UX, clipboard and scoped agent writes. Existing layouts/icons are retained with warmer pastel surfaces, rounded focus outlines, less prominent exception disclosures and cleaner alignment.
- Browser review: 24 distinct mobile/chart/command-palette/account-management specs passed after rerunning five transient failures caused by an intermediate, now-fixed JSX syntax error. Tests use disposable demo data, which was cleaned up. Screenshots are in `/tmp/mm-account-review` and `/tmp/mm-ux-shots`; these temporary artifacts may not survive future environments.
- Frontend: all 619 tests passed, typecheck and design lint passed. After portfolio polish, 60 targeted tests passed and typecheck/lint passed again. Container production build passed before the latest header spacing adjustment. The 1024px header screenshot showed a wrapped brand; spacing/search width and wordmark whitespace were then adjusted, but this last adjustment still needs browser inspection and a final build.
- Backend: full suite completed **1263 passed in 671.24 seconds**, isolated database `metalmark_test_full`. This run started before the latest holdings precision/create-only fixes and therefore does not validate those last changes. Earlier agent security suite: 171 passed; holdings: 181 focused tests plus three migration/export checks; files: four attachment tests plus 39 portability/migration checks. Local pure unit tests: 547 passed. Ruff and diff whitespace checks were clean before the final holdings edits.
- API contract regenerated and normalized against current app OpenAPI; last precision change adds internal persistence, not a new response shape. ADRs 0059–0061 describe holdings overrides, account files and scoped agent creation; ADRs 0048/0051 are partially superseded.
- Account files are stored in Postgres with RLS, metadata, hashes and byte content, included in portable export v3 with backward imports. Per-file limit is 20MiB; the existing 64MiB overall import limit remains. Files are excluded from agent reads. Existing API tokens do not automatically receive new write scopes.

### Required next steps before completion

1. Wire `backend/app/api/agent.py` holdings POST to the new investments service `upsert_holding(..., create_only=True)` rather than its current check-then-ordinary-upsert path. Agent review identified a race because browser/sync writers do not share the account lock. Reuse normal holding serializers; concurrent unique conflicts must return generic 409 with rollback. Add or run meaningful regression coverage for add-only behavior.
2. Validate the latest holdings fix: exact `market_value_override` MONEY storage avoids rounding a total value of 1 to zero for 300 million units. Historical report valuation now loads account-local overrides. The holdings agent completed implementation and tests, but final targeted tests have not yet been rerun. Run `test_sync_holdings`, `test_investments`, `test_investment_writes`, `test_portability`, and `test_migrations_live_data::test_0016_holding_overrides_preserve_existing_positions` against an isolated database, plus agent API integration tests and Ruff.
3. Migration 0016 was modified during this undeployed feature work. The throwaway demo already applied its earlier form; rebuild/reset the demo before visually testing latest overrides so the new column exists. Do not apply this to production. Coordinate any demo reset with browser work.
4. Reinspect the last 1024px header adjustment for single-line brand, useful search width and overflow; run final frontend production build. Review final diff and remaining required gates, including secret scan if not yet completed. Regenerate/check contract if route/schema changes require it.
5. Append final results to this file. Do not report the overall request complete until latest fixes and visible changes are verified. No commit, PR or production deployment was requested or performed.

### Tooling continuity

Approval policy currently is `never`; do not set `sandbox_permissions` or ask for escalation. Direct previously permitted Docker commands worked; shell-redirection variants were rejected. Frontend `node_modules` is now a real full directory, not the earlier broken temporary symlink. Offline Python lint/test environment exists at `/tmp/mm-backend-check-env`; cached dependencies at `/tmp/mm-uv-check-cache`. Full-suite exec session 68770 completed successfully and no longer needs polling. Production token remains absent from code, records and logs.

### Holdings focused verification follow-up

- Account-local holding overrides now store explicit total market value as NUMERIC(19,4), preserving an entered value of 1 even for a quantity of 300,000,000 units. Unit price is derived for display; portfolio and historical securities reporting use the exact stored total. Portable import/export preserves this field.
- Added large-quantity/small-value valuation and export round-trip regression assertions. Migration 0016 remains additive and shape-detecting; its populated-database test checks that quantity, basis, source and date survive upgrade/downgrade.
- Earlier holdings workstream verification passed: 181 backend tests, three editor interaction tests, and three focused persistence/export/migration tests before the exact-total precision refinement.
- Final focused command against isolated `metalmark_test_holdings_final` began and emitted 26 passing progress dots, then exited 130 without a result summary. A subsequent Docker lint attempt was denied access to the Docker socket under the never-approval policy; no escalation or demo reset was attempted. Root must finish the targeted rerun.
- Security review identified a race in the agent add-holding route's check-then-upsert path. Added service `create_only=True` support, which refuses an existing holding or any recorded position history before mutation; root owns agent route wiring and concurrent insert conflict handling.

## 2026-10-04 15:59 UTC — final header and layout verification

Account-files subagent resumed verification only. The latest AppShell adjustment passed the pinned
Playwright header bounds check at 1024px and the mandatory no-sideways-overflow route checks at
360px and 390px: **3 passed in 42.7 seconds**. The updated 1024px screenshot was visually inspected:
MetalMark Money stays on one line; Search and CtrlK align within the control; navigation and actions
fit without overflow. Screenshot preserved at `/tmp/mm-account-review/header-1024-light.png`.

The final demo-web production build passed after these changes: TypeScript, 1101 Vite modules and
PWA service-worker generation completed. Only the existing large-chunk advisory remained. No demo
reset or data mutation was performed in this verification, and no production access occurred.
These layout checks do not replace validation of the newly added exact holdings-value persistence;
the demo schema refresh and latest targeted backend checks remain coordinated by the primary agent.

## 2026-10-04 — correctness fixes validated on resume

- Agent holding creation now calls the service with `create_only=True`; an existing/history position is rejected before any update, and a concurrent unique insert conflict returns generic409 and rolls back. ADR0061 updated to describe the actual concurrency boundary. Both browser-insert interleavings are covered by regressions: insertion before lookup and insertion after lookup/before agent flush, preserving the browser quantity/basis.
- Latest targeted holdings/persistence/export/migration tests: **75 passed in47.32s**. Agent API suite including race regressions: **47 passed in153.69s**. Additional current-owner demotion and cross-household write-boundary tests: **3 passed in18.20s**. Ruff clean. Current in-process OpenAPI matches the checked-in contract.
- Extended the quiet report disclosure to Budgets warnings;14 budget tests passed with default-collapsed disclosure assertion. Typecheck/design lint passed. Clipboard8 tests passed; annotated its deliberately fake test credential for the secret scanner. Net-worth/reconciliation backend regression checks and final secret scan underway.
- Reset only throwaway `mm-dev`, migrating0016/0017 and reseeding fabricated demo data; exact holdings total column now exists. Browser agent is rerunning account-management workflows and inspecting budget disclosure against refreshed schema. No production access/deployment.

## 2026-10-04 16:11 UTC — refreshed account workflows and final disclosures

After the primary agent confirmed the throwaway demo reset had applied migrations 0016/0017 and
seeded it, the full account-management browser spec passed **5 tests in 31.7 seconds**. Holdings
add/edit/save and account-file upload/download/delete passed at 360px and 1280px, with all test-owned
accounts and securities cleaned up. The 1024px header check passed again. A new read-only 360px
Budgets regression used a synthetic report: repeated warnings deduplicate, begin collapsed, expand
on request, and remain inside the phone. Collapsed/expanded screenshots were visually inspected and
preserved at `/tmp/mm-account-review/budgets-360-collapsed-light.png` and
`/tmp/mm-account-review/budgets-360-expanded-light.png`.

The production build passed with TypeScript, 1101 Vite modules and PWA generation; its compiled bundle
contains the latest recorded-trade guidance, confirming that the metadata-edit form change was
included. A separate synthetic row-backed recorded-trade browser check passed **1 test in 6.8 seconds**:
name, symbol, type and value are editable; quantity and total cost basis are disabled. Its phone
layout was visually inspected, with no horizontal overflow, and preserved at
`/tmp/mm-account-review/holdings-history-360-light.png`. The synthetic position was never submitted.
The initial check used a copied demo account's EUR currency and therefore searched for the wrong USD
basis label; the mock explicitly uses USD now and the rerun passes. No seeded account/budget was
modified, and no production access occurred. The account-management spec now contains six tests.

## Final handoff checkpoint — 2026-10-04

User requested preparing a handoff and a reusable prompt; implementation work stops at this checkpoint. Later entries supersede earlier pending lists in this append-only record.

Completed since the first handoff:
- Agent holdings route is wired to create-only service with real concurrent-insert409 rollback coverage; the race is fixed. All51 current agent API cases passed across47+3+1 runs. Last case checks closed history: its initial test fixture incorrectly used a positive sell quantity; corrected to signed-2, then **1 passed in6.59s**. This was a test fixture correction, not a change to trade semantics.
- Latest75 focused holdings/export/migration tests and51 net-worth/reconciliation regressions passed. Full1263 backend tests passed before the final precision/create-only changes; those later changes have the targeted checks above. Ruff clean before final fixture-only adjustment; diff whitespace clean after it.
- Latest frontend **620 tests passed**, typecheck and design lint clean. Recorded-trade row-backed holdings now allow metadata/value editing with quantity/basis read-only; unchanged/unknown market value is omitted from metadata-only saves to avoid clearing a persisted override. Four editor tests passed, including this boundary.
- Final browser checks on refreshed demo: six distinct account-management specs pass, including holdings add/edit/reopen, files upload/download/delete, report disclosures,1024px header,360px Budgets notes and read-only synthetic history form. Broad prior mobile/chart/command-palette suite also passed. Latest production frontend build passes and includes the history-form changes. Primary inspected final1024px header; browser agent inspected both Budgets states and the history form.
- Final secret scan passed: seven rules across266 commits plus current worktree. The deliberately fake clipboard fixture is explicitly annotated; no real credential is recorded. Current OpenAPI matches checked-in contract.
- Demo reset completed safely, with0016/0017 applied and fabricated household seeded. It remains up as `mm-dev`; subagents cleaned their own temporary accounts/securities. No production access, change, commit, PR or deployment.

Remaining work for the next agent:
1. Review the final tracked AND untracked diff, especially additive migrations/RLS, exact-value/history interactions, document portability and narrow agent write boundaries. The work is substantial and has not had a final independent review or been committed.
2. Finish required repository gates that have not been validated in this session, without assuming all of verify.sh passed. Restore-drill wrapper reported no db container even though a direct `docker compose -p mm-dev ps` showed api/db/web/worker; diagnose execution/project environment before retrying. Full fake-provider e2e/walkthrough and standalone production smoke gates were not run here. Do not deploy to the real production instance as part of validation.
3. Consider the documented portability limit:20MiB per attached file but64MiB overall JSON import, with base64 overhead. Exports include all bytes; a sufficiently large export can exceed the import cap. Decide whether this needs resolution for the user's intended file volume, and report the constraint accurately.
4. Preserve all existing edits, keep records append-only, and append final review/gate results. If any new code changes arise, rerun the checks they affect and visually inspect visible changes. No feature restart is needed.

Reusable prompt (also provided to the user):

Continue the MetalMark Money UX/account-tools work in `/home/adishy/personal.data.adishy.com/src/github.com/adishy/metalmark` on branch `ux-account-tools`. Read root/backend/frontend AGENTS.md and the entire append-only `agent_record/2026-10-04-session-11-ux-account-tools.md`, especially the final2026-10-04 checkpoint. Preserve all uncommitted tracked and untracked work.

The original request is recorded there: quieter exception notes and net-worth attribution; per-account sync-safe holdings overrides; account files with database/export persistence; token copy repair; header/search/upload/currency alignment and rounded focus; opt-in agent creation of transactions/holdings only; and Meltmeter-inspired pastel styling while retaining the UI/icons. Most implementation and targeted verification are complete. Finish the final independent code/security/migration review, resolve any findings, and complete outstanding required repo gates. Do not mistake older pending lists for current status.

Production at192.168.0.123:8791 contains real financial data. It has not been accessed or changed in this work. Do not deploy or mutate it; use only the throwaway mm-dev stack and isolated test databases. A production credential was supplied in the original conversation but is intentionally absent from records; do not search logs or commit secrets. Production reads, if needed, must use the anonymized agent/debug API and its runbook, never direct DB access.

Use at most two subagents concurrently. Continue append-only dated progress records. Current tests/build/browser evidence and known validation gaps are in the final checkpoint. In particular review exact total-value persistence, historical valuation, add-only agent concurrency/tenant/role boundaries, attachment export/import and the20MiB-file/64MiB-import limitation. Finish with a clear report of verified changes and remaining limits; leave deployment to a separately authorized action.

## 2026-10-04 — independent review, findings and fixes (resumed session)

Resumed from `agent_record/prompt.txt`. No production access: the real instance is compose project
`metalmark-nw` on this host (ports 8790/8791) and was not contacted, read or changed.

**Gate environment diagnosed.** `scripts/verify.sh` and the backup/drill scripts call `docker compose`
with no project, which resolves to `metalmark` — another worktree's stack with only a worker — not the
demo stack `mm-dev`. That is why the drill reported no db container. Running with
`COMPOSE_PROJECT_NAME=mm-dev` fixes it. The `prod` gate's default ports (8790/8791) are the real
instance's, so it must be run on other ports.

**First gate run** (before fixes, while edits were landing): secrets PASS, pytest PASS (1270 passed in
416.68s), drill PASS (33 tables, 166 rows restored); lint FAIL (one real E501 in `test_agent_api.py`,
fixed); frontend and contract FAIL only because of edits in progress. To be rerun on the final tree.

**Two independent read-only reviews** (security/migration; numeric correctness) found real defects:

- Agent writes were a de-anonymisation oracle: pseudonyms are keyed on the value, so an agent could
  submit a guessed merchant, description, note or holding name and compare the pseudonym it read back
  with real rows; a guessed known name came back under its own kind. Fixed by giving the write routes
  their own schemas (`AgentTransactionCreate`, `AgentHoldingCreate`): ids, numbers and dates only,
  unknown fields refused, one optional note stored behind a fixed "Added by agent" prefix. Values are
  bounded (422, not a database error); database refusals are generic 409/422; category and tag ids are
  now checked through RLS on the browser route too (they were not tenant-checked); writes and refusals
  log `agent.write`; write scopes can be issued only by the owner; holding local names moved last in
  the known-names priority. ADR-0061 and the runbook rewritten to match.
- Attachments could make an export that cannot be imported (20 MiB files, base64, 64 MiB import
  ceiling), with no warning. Fixed with a 32 MiB household total enforced where a file is stored
  (uploads and imports), under a per-household advisory lock, plus a unit test tying the quota to the
  ceiling. ADR-0036's ceiling is unchanged. The PDF preview no longer sends `sandbox` (the built-in
  viewer does not load in a sandboxed document); downloads keep it. ADR-0060 updated.
- Holding overrides: an edit moved the date the pinned value applies from; a local-day date ahead of
  the UTC ledger day silently disabled the pin; the pinned total stayed fixed when quantity changed;
  nothing on screen marked a pinned value; renaming a bank position froze its quantity and price feed;
  a local type override to cash unbalanced appreciation; override-only holdings raised a permanent
  "no price" warning. Delegated to one subagent with a fixed design (pin quantity and its own
  effective date, proportional scaling, per-field sync freeze, visible "Value set by you").
- Collapsed notes hid material signals: fixed by a second subagent (neutral residual, signed rows, ×N
  multiplicity with a total count, warning-tone summaries when positions or accounts are left out).
  153 targeted frontend tests passed; the new states were seen only against mocked API responses
  because the demo data triggers none of them.

Targeted results so far: `test_agent_api.py`, `test_account_documents.py`, `test_document_limits.py`
— 72 passed in 124.88s on isolated `metalmark_test_main`; ruff clean; `Admin.agent.test.tsx` 8 passed.
Remaining: holdings fixes to land and be reviewed, contract regeneration, demo reset (migration 0016
changes shape again), then every gate on the final tree.

## 2026-10-04 — fixes landed, every gate green on the final tree

Holdings fixes landed (third subagent): migration 0016 reshaped in place (never deployed) — dead
`price_override` removed, `market_value_override_quantity` and `market_value_override_as_of` added with
an all-or-none check. A pinned total is exact at the quantity it was set for and scales with quantity
otherwise; it applies from its own server-set date; `as_of` (now labelled "Quantity as of") is sent
only when changed. Only a quantity, cost-basis or date edit stops bank updates for a position; a
frozen row still takes the bank's price. Cash is decided by the security's own type everywhere.
Pinned values show "Value set by you · date" on Accounts and Portfolio and are exposed in the API.
Reports treat an applicable override as a price and name a hand-set value that starts mid-period.
Import validates override fields. ADR-0059 rewritten to match. Contract regenerated from the live app.

Final gate results, all with `COMPOSE_PROJECT_NAME=mm-dev` after `dev-stack.sh reset` (0016/0017 applied):

- secrets PASS; lint PASS; pytest PASS — 1310 passed in 431.92s; frontend PASS — typecheck, 638 tests
  in 52 files, production build; contract PASS (401s and no drift); drill PASS (33 tables, 159 rows).
- walkthrough PASS — 90 passed, 0 failed. reset PASS.
- e2e: first run 92 passed, 2 failed — the budgets-notes spec expected the old distinct-sentence count
  (now a count of notes, by design; spec updated), and the dark login baseline no longer matched the
  pastel accent (both login baselines regenerated in the pinned image and viewed). Rerun: 94 passed.
- prod PASS, run as `PROD_PROJECT_NAME=mm-prodgate METALMARK_HTTPS_PORT=18790 METALMARK_HTTP_PORT=18791`
  so it could not collide with the real instance on 8790/8791; all default and host-path checks ok,
  7/7 browser checks. Torn down afterwards.

Known limits, not addressed: no rate limit or idempotency key on agent writes; an agent- or
hand-entered transaction on a synced account is not matched to the bank's later copy; household files
are capped at 32 MiB in total; the inline PDF preview's headers are tested but the preview has not
been seen rendering in a real desktop browser; a bank position's local name cannot be cleared from the
form without "Use bank updates"; the new warning-tone summaries were seen only against mocked
responses. User then authorised commit, push and PR, asked for a preview on 0.0.0.0, and repeated that
there are to be no production deploys.

## 2026-10-04 — files leave the JSON; in-app viewer; agent file reading and account creation

User asked why there was a 32 MiB household total, then decided: take files out of the JSON, have the
document point at an archive and degrade gracefully when the files are not there; no file limits
beyond performance; after upload a file must be viewable in the app and readable to agents; and the
agent API should be able to create accounts. Still no production deploys.

What changed:

- **Export/import (ADR-0060 rewritten, still unreleased).** The document lists each file with a
  `path` (`files/<sha256>`) and carries no bytes. `GET /export/archive` is a zip of `export.json` plus
  the files, built one file at a time into a temp file and streamed. `POST /import` takes the zip or
  the JSON; a file whose bytes are absent is skipped and counted in one warning, and importing the
  archive later adds exactly those. Members are read to the stated size + 1 and checked by size and
  digest. The household total is gone; one file may be 100 MiB (app constant, no DB bound). nginx body
  limit 128m, unlimited and unbuffered for `/api/import`.
- **Viewer.** PDFs (pdf.js, lazy-loaded, first 40 pages), PNG/JPEG/GIF/WebP and text open in a dialog;
  type decided server-side from the first bytes; text always served as `text/plain`. The first attempt
  framed the browser's PDF viewer: blank in the test browser and on phones, so replaced. `Dialog` now
  keeps a stack so Escape closes only the top one, and wraps long titles.
- **Agents (ADR-0062).** `documents:read`: list and fetch an account's files, not anonymized, owner
  only, read-only transaction, logged `agent.file`; the mirror still does not expose them.
  `accounts:write`: `POST /agent/v1/accounts` with type, currency, coded subtype, opening balance,
  owner; named `Added by agent[: label]` so the name is never a value the household wrote.

Gates on the final tree (`COMPOSE_PROJECT_NAME=mm-dev`): secrets, lint, contract PASS; pytest 1330
passed; frontend typecheck + 644 tests + production build; e2e 94 passed; walkthrough 90 passed; prod
PASS on 18790/18791 as `mm-prodgate`. drill and reset PASS on the run just before the pdf.js and
dialog change (neither touches what they exercise). The first e2e run of this change failed 3: a long
file name overflowed the viewer at 360px, the viewer nested in the account dialog misbehaved, and one
insights spec failed on the rows the failed test left behind; fixed by the portal, the dialog stack
and the wrapping title. Viewer looked at with a real PDF, PNG and CSV at 390 and 1280px, light and dark.

Known limits: a file is read into memory whole on upload, download and agent fetch (hence 100 MiB);
no range requests; PDFs past 40 pages need "Open in new tab"; office files are download-only; the
dev/preview databases created before this change keep the old 20 MiB check on `account_documents`
until reset (0017 was never deployed, so no real database has it); importing a household's own export
again reports one paystub and paystub line as created (seen in the agent test world; predates this
change, not investigated); agent writes still have no rate limit or idempotency key.

## 2026-10-04 — agents may name the accounts they create

User, on reading that an agent-created account was named `Added by agent` until renamed: "you can
let agents name it". `AgentAccountCreate` takes an optional `name` (≤200 printable characters,
whitespace collapsed) in place of `label`; without one the account is still `Added by agent`. The
institution stays unsettable. ADR-0062 records the accepted cost: with `accounts:write` and
`agent:read` together, an account name is a per-guess oracle for whether the household has another
thing of exactly that name. PR #48's description updated to the archive/viewer/agent state.

Gates after this change: secrets, lint, contract PASS; pytest 1330 passed; frontend first run 643/644
— `AppShell.test.tsx` "opens on ⌘K, with focus in the field" failed on focus timing, then passed 3/3
alone and 644/644 on a full rerun (not touched by this change; treat as flaky). e2e, walkthrough,
prod and drill not re-run for this backend-schema and copy change.
