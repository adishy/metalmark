# ADR 0002: Poll-based sync over LAN/VPN; no webhooks, no public ingress

- **Status:** Accepted; the "default every 6h" cadence below is refined by ADR-0028, and the rest stands.
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0001, ADR-0004, ARCHITECTURE.md §1, §5

## Context

The backend runs on a home lab for a few users. We can either expose it to the internet (enabling real-time
aggregator webhooks) or keep it private. SimpleFIN is poll-based and has no webhook model anyway.

## Decision

We will run the backend **reachable only over LAN/Tailscale (no public ingress)** and **sync by scheduled
polling** (default every 6h — **superseded by ADR-0028: 24h, floor 1h, because the source data refreshes
about daily and a 6h cron is 4× the useful rate**) plus on-demand "Sync now". No inbound webhooks.

## Consequences

- **Positive:** dramatically smaller attack surface (no public endpoints), no webhook endpoint to secure,
  matches SimpleFIN's poll model. Simpler ops.
- **Negative / costs:** sync latency up to the poll interval; a broken connection can sit stale until the next
  poll — mitigated by failure notifications (ADR-0016) and freshness badges. Remote access requires the VPN.
- **Follow-ups:** APScheduler cron + a job queue (ADR-0004); Caddy TLS + Tailscale in ops.
