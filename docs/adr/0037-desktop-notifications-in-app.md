# ADR 0037: Desktop notifications are in-app, not Web Push; the decision to notify is recorded server-side

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0002 (poll-based, LAN/VPN only), ADR-0016 (admin sync observability),
  ADR-0028 (provider seam, notification transitions), ARCHITECTURE.md §5

## Context

A bank connection that breaks should reach the person who can fix it. Today it reaches them only if
they configured `METALMARK_NOTIFY_WEBHOOK_URL` (ADR-0028) *and* are looking at wherever that webhook
lands. The app itself is silent: a connection can sit in `auth_error` for a week and nothing on screen
says so until the user opens Admin.

The obvious answer is Web Push. It is the wrong one here. Web Push requires a push service — FCM, APNs,
Mozilla — which means **internet egress** and handing a third party the timing and existence of this
household's bank activity. ADR-0002 puts the app on a LAN/VPN with no public ingress precisely so that
no such dependency exists. Web Push is also the one feature that cannot be self-hosted: there is no
"point it at your own box" option in any browser.

The remaining question is not *whether* to notify but **which of the two sinks decides**. The worker
already knows: `services/notifications.py::should_notify(previous, now)` is a pure function that says a
failure is news when there is no history, when the previous run did not fail, or when a long outage is
due a reminder (`REPEAT_AFTER`, 24 h). A second sink that re-derives that rule in TypeScript would
drift from it — and drift in the direction of notifying too often, which is the exact failure the rule
exists to prevent.

## Decision

**We will notify through the Notification API, driven by polling an authenticated endpoint, and we will
record the decision to notify on the server rather than deciding in the browser.**

1. **No Web Push.** No push service, no internet egress, no third-party metadata. The cost, stated
   plainly: notifications arrive **only while the app is open in a tab**. A closed browser gets nothing,
   and the webhook (ADR-0028) remains the route for reaching someone who is not looking.

2. **The server records each notice it decides to send.** When `should_notify` returns true, the worker
   writes a `sync_run_events` row (`event = "notified"`, `level = "info"`) carrying the notification's
   title and sanitized body, in the same transaction as the rest of the run's trail. This needs **no
   migration** — `event` is `String(64)` free text, and the row lands in the run timeline where ADR-0016
   already says "what did we tell whom" belongs. The decision is made once, in one language, by the
   function that already encodes it.

   The webhook becomes one sink of that record and the browser another. Recording happens whether or
   not a webhook is configured, so an instance with no `METALMARK_NOTIFY_WEBHOOK_URL` still notifies
   in-app.

   *Revised at implementation:* the event string is **`run.notified`**, not `notified`. Every other
   event the worker writes is namespaced by what it is about (`run.failed`, `run.finished`,
   `balance.snapshotted`, `transfers.matched`), and an event with no namespace would be the one row in
   the timeline a reader cannot place. The record's *detail* also names the trouble `trouble` rather
   than `event`, because the detail is splatted into `RunLog.emit(level, event, **detail)` and a key
   named `event` is a duplicate keyword argument — a `TypeError` in the failure path, which is the one
   path a notification must survive. It was found by a test rather than in production.

3. **The browser polls `GET /connections/notifications?since=<id>`**, a scoped, owner-only endpoint
   returning notices newer than the cursor. The cursor is the last-seen row id, held in `localStorage`.
   Each notice is shown once per browser; a notice already shown is never re-shown, because the page is
   reading a decision that was already made rather than making one.

   *Revised at implementation:* this ADR first said `/sync/notifications`, and there is no `/sync`
   namespace — the sync surface lives under `/connections` (`/connections/runs`, `/connections/jobs`),
   and a namespace invented for one endpoint would be the only thing in the app that does not read
   like its neighbours.

   A row id is not an ordering, so `since` is resolved rather than compared: the server reads that
   row's `(ts, id)` and returns events after it. `sync_run_events` is keyed by UUID and its `ts` is the
   *transaction* timestamp (the model's own docstring says so — every event in one run shares it
   exactly), so `(ts, id)` is the total order the pair gives, and the client keeps holding an opaque
   id. An id the server cannot find — pruned, or another household's — is treated as no cursor.

4. **Permission is requested from an explicit action, never on load.** A browser-native permission
   prompt that appears unbidden is the one every user reflexively denies, and a denial is permanent
   for that origin. The request is a button in Admin → Sync activity that states what will be sent
   before it asks.

5. **Displayed by the service worker**, via `registration.showNotification`. The page-context
   `new Notification(...)` constructor throws on Android Chrome, so the single code path is the one that
   works everywhere. `vite-plugin-pwa` runs in `generateSW` mode, which does not let us author the
   service worker directly, so the `notificationclick` handler ships as a static `public/sw-notify.js`
   wired in through `workbox.importScripts`. It focuses an existing tab and routes it to `/admin`, or
   opens one; it never opens a second tab when one is already there.

6. **No financial detail leaves the page to make a notification.** The body is built from the same
   sanitized `Trouble` fields the webhook sends (ADR-0028) — connection, institution, what is wrong.
   No balance, no merchant, no amount, no account number. `tag` is the connection id, so a repeat for
   the same connection replaces the standing notification instead of stacking a column of them.

7. **The icon is `notification-icon.png`** (`frontend/public/`), the same asset the PWA manifest and the
   favicon are generated from.

## Consequences

- **Positive:** a broken connection reaches the user's desktop without the app gaining an outbound
  dependency or a third party learning anything. The transition rule has exactly one implementation, so
  the webhook and the browser cannot disagree about what is news, and neither can spam.
- **Positive:** the record doubles as observability. "Did we tell anyone about Tuesday's failure?" is a
  line in the run's timeline, not a guess.
- **Negative / costs:** **only while a tab is open.** This is a real limitation, not a footnote — a
  self-hosted finance app that is usually closed gets little from this feature, and the webhook stays
  load-bearing for the asleep case. Working around it would mean either Web Push (rejected above) or a
  native client (out of scope).
- **Negative / costs:** the `public/sw-notify.js` shim is outside the TypeScript build, so it is checked
  by nothing — not typecheck, not design-lint, not the compiler. It must stay small and hand-written
  once. The intended check was an e2e assertion inside the registered worker; measurement killed that
  (below), so the shim is instead *executed* by a unit test against a fake `self` and a fake
  `clients` — which catches the failure that matters, a change that stops it registering a
  `notificationclick` handler or navigating the wrong tab.
- **Negative / costs:** a second `localStorage` cursor to get wrong. Its failure mode is benign and
  one-directional: a lost cursor re-shows notices the user has seen; a cursor that runs ahead shows
  none. Neither corrupts anything, which is why a browser-local cursor is acceptable here where a
  server-side one would not be.
- **Follow-ups:** what the e2e suite can and cannot reach, measured rather than assumed. Playwright
  drives this stack at `http://web:5173`, a plain-HTTP container hostname, and both `Notification` and
  `ServiceWorker` sit behind the browser's secure-context gate. Read out of that browser:
  `Notification.permission === "denied"`, `"serviceWorker" in navigator === false`,
  `isSecureContext === false` — and Chromium's
  `--unsafely-treat-insecure-origin-as-secure=http://web:5173` did not change any of the three. So
  `support()` answers `unsupported` in CI, the poll is disabled and the control renders its
  explanation. Consequences, all of them implemented: the e2e asserts only the environment-independent
  half (the panel always answers the notification question, never renders nothing); the
  `AppShell` → endpoint wire is covered by a unit test with the two APIs stubbed, since neither jsdom
  nor the e2e browser has them; the display path is unit-tested at the decision boundary; the endpoint
  is covered by an integration test; and the real display path is verified by hand.
  **Amended (measured later, and it corrects the conclusion above rather than the measurement):** the
  claim that this makes CI able to render only `unsupported` is too strong, and the e2e suite's
  behaviour is a consequence of the URL it uses, not of the environment. Two measurements:
  (a) the secure-context gate keys on the **hostname**, so Chromium launched with
  `--host-resolver-rules=MAP localhost <container-ip>` reaches the dev server as `http://localhost:5173`
  and reports `isSecureContext: true`, `"serviceWorker" in navigator: true` — `web:5173` is not a secure
  context, `localhost` is, and they are the same server. (The rule's target must be a literal address;
  `MAP localhost web` fails with ERR_NAME_NOT_RESOLVED.) (b) `grantPermissions(["notifications"])` is
  ignored by headless Chromium in the pinned image — permission stays `denied` — but the *same script*
  under `xvfb-run -a` headed reports `granted`. So the display path is reachable from CI after all, and
  the absence of a test for it is a decision about cost, not a fact about browsers. What stays true, and
  is why the e2e assertions are unchanged: `http://web:5173` is still an insecure context, and that is
  the origin the suite drives. ADR-0038's `prod` gate takes the other road — a production build, which
  is the half that was actually missing — and `scripts/prod_probe.cjs` carries these two facts.
- **Follow-ups (a deployment fact, not a test one):** by the same gate, **an instance reached over
  plain HTTP on a LAN hostname cannot show notifications at all** — only `https://` or `localhost` can.
  ADR-0002 puts this app on a LAN behind a VPN, which makes that the *likely* first deployment, so the
  absence has to be explained rather than left as a control that does nothing; that is why
  `unsupported` renders a sentence. Serving the app over TLS — or using it on the host itself — clears
  *this* gate, and **only this one**: see the next bullet.
- **Follow-ups (the second gate, and the one a granted browser still meets today):** secure context is
  necessary and not sufficient, because display goes through `registration.showNotification` and the
  **shipped deployment registers no service worker**. `frontend/Dockerfile` runs `CMD ["npm", "run",
  "dev"]`, so `docker compose up` serves the Vite dev server; `vite-plugin-pwa` builds a worker only for
  a production build (there is no `devOptions` in `vite.config.ts`). Measured, not inferred:
  `curl http://localhost:5173/` returns HTML whose only mention of a manifest is a comment saying
  `vite-plugin-pwa` injects the link — no `<link rel="manifest">` element, because that build never made
  one. The same tree's `npm run build` does emit `dist/sw.js`, with
  `importScripts("/sw-notify.js")` in it. So on the current deployment a granted browser has permission,
  has no worker, and `deliver()` can display nothing.
  That combination used to be rendered as **"Desktop notifications are on."** — a control reporting a
  state the page cannot reach, which is a worse lie than a button that does nothing because the reader
  has just granted a permission and watched the app confirm it worked. `notify.canShow()` now asks
  permission *and* registration, and the panel splits `granted` in two, saying where the notices actually
  are (the run history) when there is no worker. A browser with no worker is thus told so plainly, and
  `NoticePermission` is its own component precisely so that branch is reachable by a test — since neither
  jsdom nor the e2e stack has `navigator.serviceWorker` at all, the two of them can only ever render the
  `unsupported` sentence.
  **Consequence for the deployment, unresolved and deliberately not papered over:** notifications do not
  work under `docker compose up`, and cannot until the `web` service runs a production build. That is a
  container change, and the architecture doc has already settled what it looks like —
  `docs/ARCHITECTURE.md:10-18` draws `Caddy (TLS)` in front of `web (nginx) | static React PWA`, which
  clears *both* gates at once, while the compose file today serves the dev server over plain HTTP and so
  fails both. The doc and the stack disagree; this ADR records the disagreement rather than resolving it,
  because the deployment shape is its own decision and not this feature's to make.
  **Resolved, in the way this bullet predicted:** ADR-0038 makes the doc true — `docker-compose.prod.yml`
  builds `dist/` and serves it from nginx behind Caddy, and `scripts/verify.sh prod` boots that from an
  empty volume and asserts from a browser that the worker registers, which is the half this ADR could
  only describe. The dev stack is deliberately untouched, so this ADR's first bullet still holds for
  `docker compose up`: no worker, no notifications, and the panel says so.
- **Follow-ups:** if the webhook is later removed or generalised, this ADR records that it is one sink
  among several, not the owner of the notification rule.
