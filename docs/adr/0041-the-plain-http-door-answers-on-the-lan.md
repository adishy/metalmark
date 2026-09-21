# ADR 0041: The plain-HTTP door answers on the LAN, not only on loopback

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Aditya Shylesh
- **Related:** ADR-0038 decision 5 (which published this door on loopback and says why — that clause is
  superseded here, the rest of the decision stands), ADR-0039 (the standalone file this binds),
  ADR-0040 (the other overridable location, recorded the same day and with the same shape),
  ADR-0037 (the secure-context gate, measured), ADR-0002 (LAN/VPN only), `deploy/docker-compose.yaml`

## Context

ADR-0038 decision 5 published the frontend twice: Caddy on `METALMARK_HTTPS_PORT` for other devices, and
`web` on **`127.0.0.1:8791` — loopback only** for the machine running it. The reasoning was sound and is
worth restating, because none of it is being reversed: plain HTTP is acceptable on that door precisely
because it never leaves the machine, and `localhost` is a *secure context* by specification, which makes
it the one way in that needs no certificate installed and still gets a service worker.

What loopback also does is make the door **unreachable by anything that is not that machine** — including
things running *on* it. A container's `127.0.0.1` is its own; so is another compose project's. The machine
this is deployed on runs a handful of other self-hosted services and its own Caddy, which is the ordinary
shape for a home server: a reverse proxy already holding 80 and 443 for real names, with nothing else
expected to terminate TLS for itself. In that shape a published door that only the host can open is a
door its own infrastructure cannot reach, and the only way around it was to edit the deployment file —
the one file ADR-0039 says a stranger fetches and does not fork.

## Decision

**The plain-HTTP door is published on every interface by default, and the address it publishes on is a
variable so that loopback-only remains one line away.**

1. **`web` publishes `${METALMARK_HTTP_BIND:-0.0.0.0}:${METALMARK_HTTP_PORT:-8791}:8080`.** The host-IP
   field of a compose port mapping, which is where an interface belongs — `METALMARK_HTTP_BIND=127.0.0.1`
   restores exactly the behaviour of ADR-0038 decision 5. `0.0.0.0` is written as the literal default
   rather than interpolated from the environment, for the reason the file's header gives about identities:
   a value that has a dev counterpart somewhere in a `.env` must not be reachable by the deployment.

2. **What the door is for has not changed, and the README now says so next to the port variables.** The
   machine running the stack. The secure-context argument is about the *origin*, not the bind address:
   `localhost` is a secure context wherever the listener is bound, so this is still the one way in that
   needs nothing installed and still gets a service worker.

3. **The LAN can now reach an origin it cannot log in on, and that is stated rather than left to be
   discovered.** `METALMARK_ENV=prod` sets the session cookie's `Secure` flag; a browser will not keep a
   `Secure` cookie from a plain-HTTP origin that is not a secure context, so browsing to
   `http://<lan-ip>:8791` from another device loads the app and then fails to stay logged in. The failure
   looks like a wrong password, which is why it is in the file's comment and in the README rather than
   here alone. (Reasoned, not measured in this repo: it is the same secure-context gate ADR-0037 *did*
   measure for the notification API, applied to cookies.)

4. **Caddy is untouched.** It was already published on all interfaces, and it remains the door for every
   device — this decision does not make the plain-HTTP one an alternative to it, and the `Secure` cookie
   is what makes sure of that.

5. **The healthcheck keeps `http://127.0.0.1:8080/`.** That address is inside the web container's own
   network namespace, not a host bind, and it is the correct thing to dial from there. It is not an
   instance of the pattern this ADR changes, and it should not be "made consistent".

## Consequences

- **Positive:** a reverse proxy of the operator's own can front the app, as can anything else on the
  machine or the LAN, without editing the deployment file. That is the whole of what this buys.

- **Positive:** nothing that worked stops working. `http://localhost:8791` answers exactly as before, from
  the machine, over plain HTTP, with a service worker. The `prod` gate's probes are unaffected for the
  same reason — they dial `127.0.0.1`, which `0.0.0.0` includes.

- **Negative:** the app's **surface** widened while its **usable surface** did not. Anyone on the LAN can
  now reach a login form that will not accept a login. The app is LAN/VPN-only by ADR-0002 and this
  changes nothing about that, but a door that loads and cannot log you in is worse than one that refuses
  at the socket — the cost is real and is why decision 3 puts it in the two places an operator reads.

- **Negative:** an operator who wants the old behaviour has to know about a variable that did not exist.
  It is one line, and the README names it beside the two port variables, which is where someone already
  looking for "how do I move or restrict these doors" will be.

- **Follow-up:** ADR-0038's status line and `docs/adr/README.md`'s note on the default ports both call
  `8791` "the loopback http door". Both are annotated rather than edited, per the immutability rule.
