"""Anonymized, read-only access to the household for agents (ADR-0048).

* ``anonymize`` — the policies and the walker that applies them.
* ``policies`` — which policy every output field gets. A field with none is
  dropped, and a test fails until it has one.
* ``tokens`` — the bearer tokens an administrator issues.
* ``dispatch`` — resolving a token, and running an app route on its behalf.
* ``catalog`` — what ``/agent/v1`` offers, and the route each resource reads.
"""
