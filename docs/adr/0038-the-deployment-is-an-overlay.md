# ADR 0038: The deployment is an overlay on the dev stack: a built frontend, a stated environment, and TLS

- **Status:** Accepted, **superseded in part by ADR-0039** — decision 1 (an overlay on the dev stack) and
  decision 6 (how the site address reaches the Caddyfile). The file this ADR names,
  `docker-compose.prod.yml`, no longer exists: it is `deploy/compose.yaml` now. Everything else here —
  the built frontend and why it is the point, `METALMARK_ENV=prod` stated literally and what it does,
  TLS being non-optional, and the `prod` gate — stands unchanged.
- **Date:** 2026-09-20
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0037 (desktop notifications, and the deployment consequence it left open),
  ADR-0002 (poll-based, LAN/VPN only), ADR-0028 (provider seam), `docs/ARCHITECTURE.md` §1

## Context

Two things were true at once, and they contradicted each other.

`docs/ARCHITECTURE.md:10-18` draws the system as `Caddy (TLS) → web (nginx) | static React PWA`. The
compose file served Vite's dev server over plain HTTP and had no Caddy at all. ADR-0037 measured the
cost of that gap rather than asserting it: `vite-plugin-pwa` emits a service worker only for a
production build, so on `docker compose up` a browser that has granted the notification permission has
nothing to display through — and `METALMARK_ENV=dev` leaves the session cookie without `Secure`, so a
browser on any other machine could log in over http as long as it could reach the port.

The second fact is that the repo had **no artifact at all**. Nothing built `frontend/dist` outside the
`frontend` gate, no image contained one, and the only way to serve this app to a second device was to
run a development server on it. Every other milestone's "done" is a behaviour that a test can reach;
this one could not be reached by any test, because no deployment existed to test.

ADR-0037 declined to resolve this, correctly: "the deployment shape is its own decision and not this
feature's to make". This is that decision.

## Decision

**We will add a production overlay — `docker-compose.prod.yml` layered on the existing
`docker-compose.yml` — that builds the frontend, states its environment literally, and puts TLS in
front of it. The dev stack is not modified.**

1. **An overlay, not a second stack.** The same base file, the same compose project by default, so the
   same database volume and the same household: a single machine that switches between developing the
   app and using it should not end up with two ledgers. Under a second project name it becomes a
   complete, isolated deployment, which is what makes it testable next to a running dev stack.

2. **`web` gets a `prod` stage: `npm ci && npm run build`, then nginx serving `dist/`.** This is the
   point of the whole file. nginx is also what the architecture doc already drew, so the doc stops
   being aspirational. `dev` stays the default stage and `CMD`, so `docker compose up` is unchanged.

3. **nginx owns the `/api` split**, not Caddy. `client.ts` asks for `/api/...` on whatever origin
   served the page, so the deployment ends up with one origin and no CORS anywhere in it. The
   architecture doc puts the fork in Caddy; it moves one hop later, into the container that already
   serves the SPA. That is a detail of where nginx sits, not of the shape.

4. **`METALMARK_ENV=prod` is written as a literal, never as `${METALMARK_ENV:-prod}`.** This is not
   style. `.env` — the dev stack's file, committed as `.env.example` — sets `METALMARK_ENV=dev`, so an
   interpolated variable resolves to `dev` on every checkout that has one. Measured, not assumed: the
   first version of this file did exactly that, and `docker compose config` showed the deployment
   running the dev api behind nginx. What `prod` actually changes, read off the code rather than
   assumed: `auth.py` sets the session cookie's `Secure` flag from it, `main.py` drops the dev CORS
   allowance for `localhost:5173` from it, and `services/fake_simplefin.py` refuses to exist outside
   test/dev — so no environment variable can point a deployment at fabricated data.

5. **Two doors, and the difference between them is a certificate.** `web` publishes
   `127.0.0.1:8080` — loopback only — and Caddy publishes `METALMARK_HTTPS_PORT` (8443 by default).
   The loopback door is plain HTTP and that is deliberate: it never leaves the machine, and `localhost`
   is a secure context by specification, so it is the one door that needs nothing installed to reach
   and still gets a service worker. Caddy is the door for every other device, and it is not optional —
   with a `Secure` cookie a browser elsewhere cannot log in over http at all.

6. **The Caddy site address names a host.** `{$METALMARK_SITE:localhost}:443`, with `tls internal`. A
   bare `:443` — the obvious way to write "any host" — was measured and is a server with no
   certificate: the handshake is refused with `tlsv1 alert internal error`, because Caddy obtains
   certificates for the names it is *told* about and a hostname-less site tells it none. A deployment
   sets `METALMARK_SITE` to the name it will be reached by.

   **Measured afterwards, on the name rather than the default**, because the README tells an operator
   to set it and nothing here had ever done so: with `METALMARK_SITE=metalmark.test` the issued
   certificate's `subjectAltName` is `metalmark.test`; `GET /` and `GET /transactions` over
   `https://metalmark.test:8443` both answer 200 with the chain **verifying** against Caddy's root
   (`ssl_verify_result=0`, i.e. the README's trust step produces a working connection and not merely a
   clickable one); `GET /api/accounts` there is 401; and a request for a *different* name against the
   same listener is refused **at the handshake** rather than served under a name the certificate does
   not cover. The value reaches the Caddyfile because the compose file puts it in Caddy's own
   environment — a Caddyfile's `{$…}` is resolved by Caddy at startup, and compose does not interpolate
   a file it only mounts.

7. **The database is not published.** `ports: !reset []` on `db`, for two reasons that agree: a
   deployment's Postgres has no business listening on the host's interfaces, and the dev stack already
   holds 5432, so leaving it published would mean the two could never run at once.

## Consequences

- **Positive:** the gap ADR-0037 recorded is closed and, for the first time, *testable*. A built
  frontend on a real deployment registers a real service worker; `scripts/verify.sh prod` boots the
  whole deployment from an empty volume and asserts it in a browser. That gate is the only thing in
  this repo that could ever have caught the failure ADR-0037 described, and it exists because this
  decision was made.

- **Positive:** the deployment is a *thing that is verified*, not a shape in a diagram. The gate covers
  what no other gate reaches: a fresh volume, migrations, the seed, nginx's cache headers and SPA
  fallback, the `/api` prefix strip, TLS, and the login round trip. The two routes that are supposed to
  be the app are checked for the app's own mount point and not only for a 200, because a 200 says
  something answered — an nginx default page and a proxy that dropped the body both pass that and
  neither is the SPA.

- **Negative / costs:** a client must trust Caddy's local CA before any of this works in a browser,
  and it has to be *trusted* rather than clicked through — an origin with a certificate error refuses
  service workers outright, which is the same failure wearing a different hat. The README carries the
  commands. There is no way to avoid this on a LAN with no public DNS name, short of a real certificate
  from a real CA.

- **Negative / costs:** the frontend is now served two ways, so `frontend/nginx.conf` is a second place
  a path or a limit can be wrong. It found one immediately: nginx's default `client_max_body_size` is
  1 MB, and `portability.MAX_IMPORT_BYTES` accepts 64 MiB, so the largest import the app claims to
  support would have failed at the proxy with an HTML error page and no explanation. The limit is
  raised to 96 MB there, and the test that would have caught it — a 64 MiB import — does not exist.
  The `prod` gate checks what it can without one.

- **Negative / costs:** the deployment is a second environment that the gates cannot fully cover, since
  it is exercised in one place at one scale. It is a fair criticism of this ADR that "the deployment
  works" is now asserted more strongly than it is proven: the gate boots it and reads it, but a
  long-running instance has properties (backups, restarts, retention, disk) that nothing here tests.

- **Follow-ups:** ADR-0037's claim that CI could only ever render `unsupported` is falsified by
  measurement and annotated there; the `prod` gate is what replaces it. The e2e suite is unchanged, and
  still tests the environment-independent half against the dev stack.

- **Follow-ups:** `scripts/prod_probe.cjs` reaches `http://localhost:8080` by mapping the hostname to
  the container's literal address with `--host-resolver-rules`, which is what makes the origin a secure
  context in a container. If the deployment ever moves to a real TLS certificate, that trick becomes
  unnecessary and the probe should use the TLS door directly.

- **Follow-ups:** port 80 is not published, so an http:// URL typed without a scheme gets nothing
  rather than a redirect. This is a convenience left unbuilt on purpose — it is the port most likely to
  conflict on a machine also running something else — and the compose file says where to add it.
