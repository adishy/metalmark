# ADR 0042: The session cookie's `Secure` flag is a deployment decision

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0041 (whose decision 3 this makes conditional, and whose door this is the other half
  of), ADR-0037 (the secure-context gate for notifications, measured), ADR-0027 (signup), ADR-0002
  (LAN/VPN only), `deploy/docker-compose.yaml`, `backend/app/settings.py`

## Context

The session cookie has been set `Secure` whenever `METALMARK_ENV` is not `dev`, and for as long as the
deployment terminated its own TLS that was the whole story: the browser keeps a `Secure` cookie on
`https://`, drops it silently on `http://` from anything but `localhost`, and the app is reachable at
`https://…` — so the flag is free protection.

ADR-0041 already recorded the shape that breaks that: the plain-HTTP door answers on every interface, and
the LAN can therefore reach an origin where it **cannot log in**. Decision 3 there states it as a fact
about the deployment rather than a decision, and for the deployment in this repo it stays true.

The shape that made it a decision is the opposite of a LAN with no TLS: a machine whose **only** door is a
proxy of the operator's own, holding 80 and 443 for real names, on a network that is already private. The
serving host this was written against runs Caddy with `github.com/tailscale/caddy-tailscale`, is a
Tailscale subnet router, and serves every site as **`http://`** — the tunnel *is* the transport security,
which is a coherent position, and it means there is no certificate for this app to have and no CA for any
device to trust. Behind it, the app is reached at `http://metalmark.<domain>`, and the `Secure` cookie is
not a protection there. It is only a login that appears to succeed and does not stick — the failure
ADR-0041 calls worse than a door that refuses at the socket, because it looks like a wrong password.

An app-side answer was needed because no amount of deployment configuration can change it: the flag is set
by the process that mints the cookie, and `deploy/docker-compose.yaml` cannot reach into it.

## Decision

**We will make the session cookie's `Secure` flag a value the deployment states, defaulting to the
derivation it has always had, and we will not pretend that turning it off makes the origin secure.**

1. **`METALMARK_SESSION_COOKIE_SECURE` takes `auto` (the default), `true` or `false`.** `auto` keeps the
   historical rule — `Secure` whenever `METALMARK_ENV` is not `dev` — so *every existing deployment,
   including both halves of the `prod` gate, behaves exactly as before*. The deployment passes it through
   as `${METALMARK_SESSION_COOKIE_SECURE:-auto}` in the api service. `true`/`false` are read through the
   same boolean parsing as every other boolean setting (`1`, `yes`, `on` work); anything else is a
   validation error naming the variable, because a setting whose failure mode is a silently weaker cookie
   must not fail quietly.

   Three values rather than two on purpose. A boolean field cannot distinguish "unset" from `false`, and
   unset is the case that has to keep deriving the answer. Blank parses as unset rather than as `false`
   for the same reason, since the two places an operator writes it — an empty value in `.env`, and the
   compose interpolation — are far more likely to mean "leave it alone".

2. **It changes the cookie, not the origin.** A service worker, and with it notifications, the install
   prompt and offline use, is refused by the browser on a non-secure origin whatever this variable says:
   that gate is about where the document was served from (ADR-0037 measured it). So the deployment this
   exists for gets a working login and still no notifications, and the README says so where it tells the
   operator to set it.

3. **The cost is stated as a cost, and it is about the network rather than about the app.** With the flag
   off, the session token crosses that network readable by anything on the path — and an attacker on the
   path can also *set* a cookie, since a plain-HTTP response is theirs to rewrite. On a tailnet that
   cleartext is inside WireGuard, which is why this is the right trade there; on a LAN it is every device
   on the segment, and the honest instruction is that this is for a network you would already trust with
   the traffic. It does not change what can *reach* the app: ADR-0041 published that door already.

4. **Rejected: deriving it from `X-Forwarded-Proto`.** The app cannot see the client's scheme at all —
   `backend/Dockerfile` starts uvicorn without `--proxy-headers`, so the header is not trusted and not
   read, and the frontend's nginx deliberately does not set one. Making that the input would mean turning
   on proxy-header trust, which puts a client-supplied header in charge of a cookie's security
   attributes, and would make the correct answer depend on a proxy configuration this repo does not own.
   A stated value in `.env` is worse only in that someone has to state it.

5. **Rejected: inferring it from the request's `Host` or the bind address.** Both are guesses about a
   deployment from inside the process. `localhost` is a secure context on a LAN address's port, an IP
   that "looks private" is still `http://`, and the one thing an operator can be sure of — they know
   whether they installed a certificate — is exactly the thing a heuristic cannot read.

6. **The default door does not move.** Caddy with `tls internal` stays the deployment's recommendation,
   `auto` keeps marking the cookie `Secure` there, and the README's install path is unchanged. This ADR
   adds a door for a topology that has its own transport, not a replacement for that one.

## Consequences

- **Positive:** an instance reachable only over plain HTTP on a private network is now usable rather than
  broken in a way that looks like a password problem. That is the entirety of what it buys.

- **Positive:** the default moved zero bytes. Measured by the `prod` gate on the deployment as it ships:
  with the variable unset, the login over TLS still returns a `Set-Cookie` carrying `Secure`, and the
  authenticated read that follows still answers 200.

- **Negative / costs — the session token is readable, and plantable, by anything on the path.** This is
  not a subtle caveat and it is not conditional on the app: it is what the flag was doing. A snapshot of
  the network is a snapshot of a live session, and a `Set-Cookie` injected into a plain-HTTP response is a
  session an attacker chose. The README states it next to the variable, in the terms of the two networks
  this is meant for.

- **Negative / costs — notifications stay off, and that is the sharper disappointment of the two.** The
  setting is asked for at exactly the deployment where an operator might expect it to unlock the rest of
  the secure-context features, and it does not: `frontend/src/lib/notify.ts`'s `canShow()` still refuses,
  `/admin` still explains why, and the panel still shows no switch. Both the README and the compose
  comment say so rather than leaving it to be discovered.

- **Negative / costs — one more thing an operator can get wrong, with a failure that looks like a
  different one.** `false` on a deployment that *does* terminate TLS silently downgrades it. The name
  argues against that reading (`false` is not the default and the compose file spells the default out),
  and the value is stated rather than inferred, so the mistake is at least a visible line in `.env`.

- **Follow-ups:** `scripts/verify.sh`'s `prod` gate measures both values against the running deployment —
  the default (already asserted: `Secure` present on the login over TLS) and the opt-out, by exporting
  `false`, recreating only the api, and asserting a `Set-Cookie` that arrives *without* `Secure`, then
  restoring the variable. Recreating rather than booting a third time keeps the claim end-to-end while
  costing seconds; the value still has to cross the compose interpolation, the container boundary and the
  request path to be seen. Measured on the working tree this was written in: the gate prints both lines
  green (`the session cookie is Secure` and `…and not Secure when the deployment says so`, each with the
  value redacted) and takes 72s against 65s before the phase existed. A third boot would have been ~65s
  more for the same two lines. `backend/tests/unit/test_session_cookie.py` asserts the resolution rules and the
  header the app emits, both ways, without a database.

- **Follow-ups:** two accepted records name the flag as a consequence of `METALMARK_ENV` rather than as a
  decision, and both are annotated in their status line rather than edited, per the immutability rule:
  ADR-0038 decisions 4 and 5 (decision 4's "`auth.py` sets the session cookie's `Secure` flag from it", and
  decision 5's "with a `Secure` cookie a browser elsewhere cannot log in over http at all") and ADR-0041
  decision 3 with its "the app's *surface* widened while its *usable* surface did not" consequence. Each is
  the *default* now. Neither the statement that `METALMARK_ENV=prod` marks the cookie, nor ADR-0041's
  secure-context reasoning about `localhost`, is affected.
