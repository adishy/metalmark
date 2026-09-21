# ADR 0001: SimpleFIN as the sole aggregation provider, behind a pluggable interface

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0002, ADR-0009, ARCHITECTURE.md §3

## Context

We need to auto-sync balances and transactions for US banks, brokerages, and TreasuryDirect on a
self-hosted, privacy-respecting system. Options: Plaid/MX/Finicity (what the big hosted apps use — best
coverage but hosted, they see your data, and production needs a business application), or SimpleFIN
Bridge (the self-hosting community standard: $15/yr, read-only, US+Canada, poll-based,
privacy-friendly). SimpleFIN's
protocol carries balances + transactions but no investment holdings, and TreasuryDirect coverage is unreliable.

## Decision

We will use **SimpleFIN Bridge as the only aggregator for v1**, accessed through an `AggregatorProvider`
interface so a second provider (e.g., Plaid) can be added later without a rewrite. Coverage gaps are closed by
manual entry and CSV/OFX import (ADR-0010), not by adding a provider now.

## Consequences

- **Positive:** cheap, private, no business approval, no credential storage (SimpleFIN holds them). One simple
  protocol to integrate. Interface keeps us un-locked.
- **Negative / costs:** no holdings data (ADR-0011 makes holdings manual/first-class), unreliable
  TreasuryDirect, read-only. We accept a manual fallback for the long tail.
- **Follow-ups:** implement only `SimpleFinProvider`; keep the interface honest with a fake provider in tests.
