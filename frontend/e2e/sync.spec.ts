import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

// The SimpleFIN vertical, through the browser: claim a bank, sync it, read the
// run back, and operate it from the panel.
//
// This suite needs a stack whose `METALMARK_SIMPLEFIN_PROVIDER` is `fake`, which
// is what `docker-compose.yml` passes through and what the `e2e` job in
// `.github/workflows/ci.yml` sets. Without it the claim endpoint trades the token
// for a live bridge credential, and a test that needs a real bank — and real
// money — to pass is not a test anyone can run. Run it locally the same way:
//
//     METALMARK_SIMPLEFIN_PROVIDER=fake docker compose up -d --build
//
// What the fake gives this file is the whole path minus the network: claim →
// connection row → queued job → worker → a run with counters and a log. What it
// cannot give is any of the *failure* states — a revoked credential, a lapsed
// subscription, a payload that does not parse. Those stay integration tests
// (`backend/tests/integration/test_sync.py`), because a provider asked to be both
// a happy path and a fault injector from a browser proves neither.
//
// The tests share one connection, created by the first. Deliberate: the
// connection *is* the fixture, re-creating it per test would only prove the claim
// path three times, and a second connection would remap the first one's accounts
// onto itself — ADR-0009 keys an account on institution + name, not on the
// connection it arrived through, so a second sync of the same demo capture is a
// reconnect, not a new bank.

/** Ids from a set of `data-testid="{prefix}-{id}"` rows, in DOM order. */
async function rowIds(page: Page, prefix: string): Promise<string[]> {
  return page.$$eval(
    `[data-testid^="${prefix}-"]`,
    (els, p) => els.map((el) => (el.getAttribute("data-testid") ?? "").slice(p.length + 1)),
    prefix,
  );
}

/** The id of the row that appeared since `before` — how a spec running against a
 *  long-lived database (CI retries, a developer's stack) finds *its* row rather
 *  than the first one, which belongs to whoever ran last. */
async function appearedSince(
  page: Page,
  prefix: string,
  before: string[],
): Promise<string | undefined> {
  return (await rowIds(page, prefix)).find((id) => !before.includes(id));
}

/** Poll `read` until it answers, or `ms` elapses — `undefined` on timeout.
 *
 *  Unlike `expect.poll`, running out of time is not a failure: the job race below
 *  has two correct outcomes and only one of them puts a row on the screen. */
async function waitFor(
  page: Page,
  read: () => Promise<string | undefined>,
  ms: number,
): Promise<string | undefined> {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await read();
    if (value !== undefined) return value;
    if (Date.now() >= deadline) return undefined;
    await page.waitForTimeout(500);
  }
}

async function openConnectionsTab(page: Page): Promise<void> {
  await page.goto("/settings");
  await page.getByTestId("settings-tab-connections").click();
  await expect(page.getByTestId("settings-panel-connections")).toBeVisible();
  // The list is a *query*, and the panel is visible while it is still in flight.
  // Reading the row ids before it settles reads an empty list, and then every
  // "the row that appeared" assertion below is off by one — it picks whichever
  // connection was already there and the test silently acts on the wrong one.
  // (It passes on a fresh database, which is exactly what makes it worth a wait.)
  await expect(
    page.getByTestId("connection-list").or(page.getByTestId("no-connections")),
  ).toBeVisible();
}

/** Claim a bank and return its connection id. The token is arbitrary: the fake
 *  accepts any and the real provider is not reachable from CI, so the assertion
 *  worth making is about what the *claim* produces, not about the token. */
async function connect(page: Page, token: string): Promise<string> {
  const before = await rowIds(page, "conn-row");
  await page.getByTestId("setup-token").fill(token);
  await page.getByTestId("connect-submit").click();
  await expect
    .poll(async () => (await rowIds(page, "conn-row")).length, { timeout: 15_000 })
    .toBe(before.length + 1);
  const id = await appearedSince(page, "conn-row", before);
  expect(id, "the claim returned a row with no id").toBeTruthy();
  return id!;
}

/** The id of the newest run for the connection currently in the filter. */
async function newestRun(page: Page): Promise<string> {
  const [first] = await rowIds(page, "run-row");
  expect(first, "no run row to read").toBeTruthy();
  return first;
}

// Shared because the connection is the fixture — see the header. Playwright runs
// one file's tests in order in a single worker (`fullyParallel: false`,
// `workers: 1`), so test 1 has always finished before test 2 starts.
let connectionId: string;

test.describe("bank sync", () => {
  test("a claimed bank syncs once, and syncing it again changes nothing", async ({ page }) => {
    // The run history polls every 15 s (`src/api/sync.ts` — nothing invalidates it
    // when the *server* starts a run), so this test deliberately waits on the
    // server's work rather than on a click, and needs more than the default 30 s.
    test.slow();

    const token = `e2e-setup-${Date.now()}`;
    await login(page);
    await openConnectionsTab(page);
    connectionId = await connect(page, token);

    // A fresh claim is healthy, unpaused, and has no error to show. The absence
    // is the assertion: `last_error` is what the dashboard renders as a failure,
    // and a claim that half-succeeded would put something there.
    await expect(page.getByTestId(`conn-status-${connectionId}`)).toHaveText("Working");
    await expect(page.getByTestId(`conn-paused-${connectionId}`)).toHaveCount(0);
    await expect(page.getByTestId(`conn-last-error-${connectionId}`)).toHaveCount(0);

    // The link into the panel from the tab that owns the credential. It used to
    // be the *only* way to reach `/admin`; the panel is now a nav destination
    // for administrators and has its own Settings tab, and this link is the
    // third door — the one someone standing in Connections would look for.
    await page.getByTestId("open-admin").click();
    await expect(page.getByRole("heading", { name: "Sync activity" })).toBeVisible();
    await expect(page.getByTestId(`conn-row-${connectionId}`)).toBeVisible();

    // Scope the history before syncing, so the run this waits for cannot be
    // satisfied by a row an earlier run of this spec left behind.
    await page.getByTestId("run-filter").selectOption(connectionId);
    await expect(page.getByTestId("no-runs")).toBeVisible();

    await page.getByTestId(`sync-now-${connectionId}`).click();

    await expect
      .poll(async () => rowIds(page, "run-row"), { timeout: 45_000 })
      .not.toHaveLength(0);
    const first = await newestRun(page);
    // `ok`, not `done`: a *run* is ok/partial/error/cancelled and a *job* is
    // queued/running/done/cancelled/expired. Waiting for "done" here would wait
    // for a word this element never renders.
    await expect(page.getByTestId(`run-row-${first}`)).toContainText("ok", { timeout: 45_000 });

    // The run logged its work, in the sanitized trail the panel expands, and it
    // resolved the payload into accounts.
    //
    // Deliberately *not* "inserted > 0". The ledger is shared, so on any database
    // where another connection has already synced this capture the rows are
    // present and the correct count is zero — ADR-0009 keys an account on
    // institution + name rather than on the connection, so a second connection
    // reporting the same accounts is a *reconnect*, not a new bank. Asserting a
    // non-zero insert asserts that the database is empty, which is a fact about
    // the environment and not about the code. The ingest guarantee that does hold
    // everywhere is asserted below, on the second run.
    await page.getByTestId(`run-toggle-${first}`).click();
    await expect(page.getByTestId(`run-detail-${first}`)).toBeVisible();
    await expect(page.getByTestId(`run-events-${first}`).locator("li").first()).toBeVisible();
    await expect(page.getByTestId(`run-detail-${first}`)).toContainText("run.finished");

    const detail = (await page.getByTestId(`run-detail-${first}`).textContent()) ?? "";
    const counter = (term: string) =>
      Number(new RegExp(`${term}\\s*(\\d+)`).exec(detail)?.[1] ?? "0");
    expect(counter("Accounts seen")).toBeGreaterThan(0);
    // Every account the provider reported was either created or matched to an
    // existing one. Zero of both would mean the account path silently dropped
    // them, and the run would still have reported `ok`.
    expect(counter("Accounts created") + counter("Accounts remapped")).toBeGreaterThan(0);

    // The institution's name arrives with the first successful fetch, not with the
    // token — the claim has nothing to read it from. So this is the assertion that
    // the *sync* wrote it back, and it is also what makes the run's own
    // `connection_label` readable after the connection is gone.
    await expect(page.getByTestId(`conn-row-${connectionId}`)).not.toContainText(
      "Unnamed connection",
    );

    // The headline property of the whole workstream: a second sync of the same
    // data inserts nothing and updates nothing. A re-run that reports work is
    // either duplicating history or rewriting fields a human owns.
    const previous = await newestRun(page);
    await page.getByTestId(`sync-now-${connectionId}`).click();
    await expect
      .poll(async () => newestRun(page).catch(() => previous), { timeout: 45_000 })
      .not.toBe(previous);
    const second = await newestRun(page);
    const secondRow = page.getByTestId(`run-row-${second}`);
    await expect(secondRow).toContainText("ok", { timeout: 45_000 });
    // Anchored, and on the summary paragraph specifically — a bare substring
    // match for "0 new" is satisfied by "10 new", which is the one number this
    // assertion exists to rule out.
    await expect(secondRow.locator("p").first()).toHaveText(/^0 new · 0 updated/);
  });

  test("the panel pauses, retunes, and resumes the connection", async ({ page }) => {
    expect(connectionId, "test 1 must have created the connection this one acts on").toBeTruthy();
    await login(page);

    // Arrive through the nav item rather than by URL, so the door an
    // administrator actually uses is the one this test walks through. The panel
    // shipped with no nav entry and no occurrence of the word "Admin" anywhere
    // in the interface; both halves of that are asserted here.
    const adminNav = page.getByTestId("nav-admin");
    await expect(adminNav).toBeVisible();
    await adminNav.click();
    await expect(page.getByRole("heading", { name: "Sync activity" })).toBeVisible();

    const row = page.getByTestId(`conn-row-${connectionId}`);
    const syncNow = page.getByTestId(`sync-now-${connectionId}`);
    const pause = page.getByTestId(`pause-${connectionId}`);
    const interval = page.getByTestId(`interval-${connectionId}`);
    await expect(row).toBeVisible();

    // Pausing is a scheduling gate, not a health state, so it reads as a second
    // chip beside the status rather than replacing it.
    await pause.click();
    await expect(page.getByTestId(`conn-paused-${connectionId}`)).toBeVisible();
    await expect(pause).toHaveText("Resume");
    await expect(page.getByTestId(`conn-status-${connectionId}`)).toHaveText("Working");
    await expect(syncNow).toBeDisabled();

    await pause.click();
    await expect(page.getByTestId(`conn-paused-${connectionId}`)).toHaveCount(0);
    await expect(syncNow).toBeEnabled();

    // The cadence is per connection and takes effect immediately. The label is
    // rendered from the value the *server* returned, so seeing it change is proof
    // the PATCH round-tripped rather than that the select moved.
    await interval.selectOption("1440");
    await expect(row).toContainText("every 1 d");
    await interval.selectOption("720");
    await expect(row).toContainText("every 12 h");
  });

  test("cancelling a job answers truthfully, whichever side of the race it lands", async ({
    page,
  }) => {
    // Waits out the 5 s job poll twice over; see the comment below.
    test.slow();
    expect(connectionId, "test 1 must have created the connection this one acts on").toBeTruthy();
    await login(page);
    await page.goto("/admin");
    await expect(page.getByTestId(`conn-row-${connectionId}`)).toBeVisible();

    // Cancel is a race against a sync that finishes in milliseconds, and there is
    // no way to hold the worker still from a browser. So what is asserted is the
    // invariant rather than a fixed side of the race: clicking Cancel always
    // produces exactly one of the two documented answers — the job is gone
    // (cancelled while queued or running) or the panel says it had already
    // finished — and never a stale row, a silent no-op, or a hung request.
    //
    // The 409 path is the one the design accepts as worst case: ingest is
    // idempotent, so a cancel that lands after the commit did not undo anything.
    const before = await rowIds(page, "job-row");
    await page.getByTestId(`sync-now-${connectionId}`).click();

    // The queue is polled every 5 s, so a job that lives for less than one poll
    // is legitimately never seen. That is not a skip: the branch below asserts
    // what must then be true instead.
    const jobId = await waitFor(page, () => appearedSince(page, "job-row", before), 12_000);

    if (jobId) {
      await page.getByTestId(`cancel-${jobId}`).click();
      await expect
        .poll(
          async () => {
            if ((await page.getByTestId(`job-row-${jobId}`).count()) === 0) return "gone";
            if (await page.getByTestId("admin-error").isVisible()) return "refused";
            return "pending";
          },
          { timeout: 20_000 },
        )
        .not.toBe("pending");

      if (await page.getByTestId("admin-error").isVisible()) {
        // Refused because it had already finished. The wording matters: the
        // operator is owed the truth about their data, not a comforting "cancelled".
        await expect(page.getByTestId("admin-error")).toContainText(/already finished/i);
      } else {
        await expect(page.getByTestId(`job-row-${jobId}`)).toHaveCount(0);
      }
    } else {
      // The job ran and finished inside one poll interval, so it was never in the
      // live table. The proof that it ran is the run it left behind.
      await expect(page.getByTestId("no-jobs")).toBeVisible();
      await page.getByTestId("run-filter").selectOption(connectionId);
      await expect
        .poll(async () => rowIds(page, "run-row"), { timeout: 45_000 })
        .not.toHaveLength(0);
    }

    // Either way the queue is empty afterwards: nothing was left behind in a
    // state the panel would have to explain on the next visit.
    await expect(page.getByTestId("no-jobs")).toBeVisible({ timeout: 30_000 });
  });
});
