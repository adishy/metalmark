"""A scripted stand-in for the bridge, so sync is testable without a network.

This module ships in the application rather than living under ``tests/``: the
claim endpoint has to work on a fresh checkout and in CI, where there are no
SimpleFIN credentials and there must not be. ``METALMARK_SIMPLEFIN_PROVIDER=fake``
is what makes "connect a bank" a button a developer can press on an empty
machine.

**Scripted, not canned.** A fake that returns one hard-coded payload tests that
the code runs, not that it is right. The provider here is driven by a queue of
``AccountSet`` objects the caller supplies — which is what lets a test say "the
id changed between two fetches" or "the second fetch 403s" and mean it.

It also **records every credential it was handed**. That is not bookkeeping for
its own sake: the standing requirement for this workstream is that the access
URL never reaches a log, an error message or a run event, and the way to test
that is to hold the exact string and then search everything the run produced for
it. A fake that swallowed its input would make that test impossible to write.

Importability is deliberately *not* the safety boundary — the image is built
with ``COPY . .``, so ``tests/`` ships too and anything importable is
reachable in production. ``fake_provider_allowed()`` is the boundary, and
``get_provider`` refuses rather than warns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.services.aggregator import AccountSet, ClaimResult, ProviderError
from app.services.simplefin import parse_accounts_payload

#: Reserved TLD (RFC 2606) and obvious placeholder credentials, so this can
#: never resolve and can never be mistaken for a real secret — while still being
#: a single exact string a test can grep every log record for.
FAKE_ACCESS_URL = "https://fake-user:fake-password@fake-bridge.invalid/simplefin/"

#: Mirrors the shape of a real setup token (base64 of a claim URL) closely
#: enough that the claim path exercises its decode, without being one.
FAKE_SETUP_TOKEN = "aHR0cHM6Ly9mYWtlLWJyaWRnZS5pbnZhbGlkL3NpbXBsZWZpbi9jbGFpbS9GQUtF"

#: Environments where the fake may be selected. Anything else — staging,
#: production, an unset variable — refuses.
FAKE_ALLOWED_ENVS = ("test", "dev")

CAPTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "simplefin" / "demo_capture.json"
)


def fake_provider_allowed() -> bool:
    """Whether this process may use the fake provider at all."""
    from app.settings import get_settings

    return get_settings().env in FAKE_ALLOWED_ENVS


def load_demo_capture() -> AccountSet:
    """The committed demo capture, read by the **production** parser.

    Deliberately routed through ``parse_accounts_payload`` rather than
    constructed by hand: the fixture is a real capture precisely so it validates
    our reading of the wire format. A fake that built DTOs directly would agree
    with itself and prove nothing.

    A missing or unreadable fixture yields an empty set rather than raising. The
    alternative is that a trimmed deployment cannot start its worker because a
    *test fixture* is absent, which is a worse failure than an empty demo.
    """
    try:
        return parse_accounts_payload(CAPTURE_PATH.read_bytes())
    except (OSError, ProviderError):
        return AccountSet()


@dataclass
class FakeProvider:
    """An ``AggregatorProvider`` that answers from a script.

    ``script`` semantics, which differ by how you construct it:

    * ``script=None`` (the dev default) — serve the committed demo capture, and
      keep serving it. A seeded connection therefore syncs forever without
      running dry: on the second sync it reports ``inserted == 0,
      updated == 0``, which is the idempotency demonstration, not a bug.
    * ``script=[...]`` — pop one set per fetch, in order. Once exhausted, serve
      empty sets, because a test that runs out of script should fail visibly
      rather than silently receive yesterday's data. Pass ``repeat_last=True``
      to keep serving the final entry instead.
    """

    script: Sequence[AccountSet] | None = None
    access_url: str = FAKE_ACCESS_URL
    #: Raised instead of returning, once. Set to ``None`` between calls to test
    #: "failed, then recovered" — which is what the notify-on-transition rule
    #: needs in order to be tested at all.
    raise_on_fetch: ProviderError | None = None
    repeat_last: bool = False

    name: str = "fake"
    #: Every setup token ``claim`` was given, for asserting none was logged.
    seen_setup_tokens: list[str] = field(default_factory=list, init=False)
    #: Every access URL ``fetch_accounts`` was given, for the same reason.
    seen_access_urls: list[str] = field(default_factory=list, init=False)
    fetch_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.script is None:
            self.script = (load_demo_capture(),)
            self.repeat_last = True

    async def claim(self, setup_token: str) -> ClaimResult:
        self.seen_setup_tokens.append(setup_token)
        return ClaimResult(access_url=self.access_url)

    async def fetch_accounts(self, access_url: str, *, start: datetime) -> AccountSet:
        self.seen_access_urls.append(access_url)
        if self.raise_on_fetch is not None:
            raise self.raise_on_fetch

        script = self.script or ()
        index = self.fetch_count
        self.fetch_count += 1

        if index < len(script):
            return script[index]
        if self.repeat_last and script:
            return script[-1]
        return AccountSet()

    def reset(self) -> None:
        """Forget the script position and the recorded credentials."""
        self.fetch_count = 0
        self.seen_setup_tokens.clear()
        self.seen_access_urls.clear()


__all__ = [
    "FAKE_ACCESS_URL",
    "FAKE_ALLOWED_ENVS",
    "FAKE_SETUP_TOKEN",
    "FakeProvider",
    "fake_provider_allowed",
    "load_demo_capture",
]
