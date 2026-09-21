// Does a *built* MetalMark actually register a service worker?
//
// This is the one claim in the repo that had no test at all until now, and it is
// the entire reason the `prod` image exists. `vite-plugin-pwa` emits `sw.js` only
// for a production build, so on the dev stack — every `docker compose up` —
// `navigator.serviceWorker.ready` never settles, and the app says so on /admin
// rather than offering a switch that would change nothing (ADR-0037). Asserting
// this from a real browser against the real production image is the only way to
// know the deployment closed that gap.
//
// Two things make it possible, both measured rather than assumed:
//
//   * `--host-resolver-rules` lets the page's origin be `http://localhost:<port>`
//     while the connection lands on a container on the compose network. That
//     matters because a secure context is decided by the *hostname*, and
//     `localhost` is on the spec's potentially-trustworthy list however it
//     resolves. The alternative — `http://web:8080` — is not a secure context,
//     and service workers are refused there. (The rule's target must be a
//     literal address; `MAP localhost web` fails with ERR_NAME_NOT_RESOLVED.)
//   * Nothing here needs a certificate. `http://localhost` is a secure context
//     without one, which is why the deployment publishes the loopback door as
//     well as Caddy's.
//
// `@playwright/test` is required by absolute path: this file is mounted outside
// the frontend checkout that `npm ci` installs into, and the Playwright image has
// no global `playwright` package.
const { chromium } = require("/work/node_modules/@playwright/test");

const [TARGET, HOST, ADDR] = process.argv.slice(2);

if (!TARGET || !HOST || !ADDR) {
  console.error("usage: prod_probe.cjs <url> <host-to-map> <address>");
  process.exit(2);
}

const checks = [];
function check(ok, label, detail) {
  checks.push({ ok, label, detail });
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
}

(async () => {
  const browser = await chromium.launch({
    args: [`--host-resolver-rules=MAP ${HOST} ${ADDR}`],
  });
  const page = await browser.newPage();

  await page.goto(TARGET, { waitUntil: "domcontentloaded", timeout: 30000 });

  // The app boots and routes itself, which can destroy the execution context
  // mid-evaluate — so a read that throws is retried rather than fatal.
  const read = (fn) => page.evaluate(fn);
  let facts = null;
  for (let i = 0; i < 20 && facts === null; i++) {
    facts = await read(() => ({
      href: location.href,
      secure: window.isSecureContext,
      hasWorkerApi: "serviceWorker" in navigator,
      manifest: document.querySelector('link[rel="manifest"]')?.getAttribute("href") ?? null,
      rootChildren: document.getElementById("root")?.childElementCount ?? -1,
    })).catch(() => null);
    if (facts === null) await page.waitForTimeout(500);
  }

  if (facts === null) {
    console.error("the page never settled");
    process.exit(1);
  }

  check(facts.secure, "the page is a secure context", facts.href);
  check(facts.hasWorkerApi, "navigator.serviceWorker exists");
  check(
    facts.manifest !== null,
    "index.html declares a web app manifest",
    String(facts.manifest),
  );
  check(facts.rootChildren > 0, "the SPA rendered", `#root has ${facts.rootChildren} children`);

  // The heart of it. `ready` is a promise that only settles once a worker is
  // active for this page's scope — on a page that never gets one it simply never
  // settles, which is exactly the dev stack. So it is raced against a timeout
  // rather than awaited.
  const registration = await Promise.race([
    page
      .evaluate(async () => {
        const reg = await navigator.serviceWorker.ready;
        // `ready` resolving is not the same as the worker being finished: on a
        // first install it is routinely still `activating` at that moment, which
        // is a state it leaves on its own a moment later. Measured, not
        // assumed — the first version of this check read the state once and
        // failed on a worker that was in fact installing correctly.
        const worker = reg.active;
        if (worker && worker.state !== "activated") {
          await new Promise((resolve) => {
            const timer = setTimeout(resolve, 15000);
            worker.addEventListener("statechange", function onChange() {
              if (worker.state === "activated") {
                clearTimeout(timer);
                worker.removeEventListener("statechange", onChange);
                resolve();
              }
            });
          });
        }
        return {
          scriptURL: reg.active?.scriptURL ?? null,
          state: reg.active?.state ?? null,
          scope: reg.scope,
        };
      })
      .catch((e) => ({ error: String(e.message).split("\n")[0] })),
    new Promise((resolve) => setTimeout(() => resolve({ timeout: true }), 30000)),
  ]);

  if (registration && registration.timeout) {
    check(false, "a service worker became active", "navigator.serviceWorker.ready never settled");
  } else if (registration && registration.error) {
    check(false, "a service worker became active", registration.error);
  } else {
    check(
      registration.state === "activated",
      "a service worker became active",
      `${registration.state} ${registration.scriptURL}`,
    );
    check(
      typeof registration.scriptURL === "string" && registration.scriptURL.endsWith("/sw.js"),
      "it is the worker the build emitted",
      String(registration.scriptURL),
    );
    check(
      registration.scope === new URL(TARGET).origin + "/",
      "its scope covers the app",
      String(registration.scope),
    );
  }

  await browser.close();

  const failed = checks.filter((c) => !c.ok).length;
  console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => {
  console.error(`the probe itself failed: ${e && e.message}`);
  process.exit(1);
});
