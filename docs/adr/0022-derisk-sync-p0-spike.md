# ADR 0022: De-risk sync early — a P0 SimpleFIN spike and a Phase-1 vertical slice

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** household + Claude
- **Related:** ADR-0010, ADR-0015, ARCHITECTURE.md §3, §7

## Context

Manual-first (ADR-0010) proves the model is **self-consistent**, not that it's **SimpleFIN-compatible**. The
hardest sync logic — `external_id`/`external_key` reconnect remap, pending→posted id-instability, low-water-
mark fetch, the `provider` provenance branch — has no Phase-1 manual analog. Hand-authored fixtures encode the
assumptions we already hold, so they can't catch a wrong assumption. If the SimpleFIN payload doesn't fit the
model, we'd discover it in Phase 2 — after the P0 schema is "frozen."

## Decision

- **P0 sandbox spike (before the schema freeze):** hit the real SimpleFIN demo token, capture actual payloads,
  and validate the DTO→ledger mapping + provenance + remap assumptions against **real** data. The test
  fixtures (ADR-0015) are **derived from these captures**, not invented.
- **Phase-1 vertical slice:** pull a thin end-to-end SimpleFIN path (claim → fetch → insert → reconnect-remap)
  into late Phase 1 as a spike, so remap/pending behavior is proven before the schema is fully locked.

## Consequences

- **Positive:** the frozen P0 contract is validated against reality; fixtures test real behavior; Phase 2 can't
  invalidate the freeze.
- **Negative / costs:** a little sync work moves earlier, softening the clean "all manual, then all sync"
  split — a deliberate trade for correctness.
- **Follow-ups:** capture artifacts checked into fixtures; the vertical slice is a Phase-1 exit item.
