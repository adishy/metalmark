"""The aggregator seam (ADR-0001): what sync needs from a bank, in our words.

Nothing downstream of this module sees a provider's wire shape. SimpleFIN calls
an account id ``id``, a posted time ``posted``, and marks a pending row by
sending ``posted: 0``; a different aggregator would use a boolean. Normalising
once, here, is what keeps that vocabulary from leaking into the ledger — and
what makes ``FakeProvider`` a real substitute for the network rather than a
second implementation to keep in step.

Two conventions the DTOs enforce rather than merely document:

* **Money is ``Decimal``, parsed from the provider's string.** Never via float.
  A float round-trip through ``1e-4`` is how a cent disappears from a ledger.
* **Instants are aware UTC datetimes.** A naive datetime in a ledger that spans
  timezones is a bug waiting for a DST boundary.

Deliberately *not* here: which provider a connection uses, what a failure means
for its health, and when to sync it. Those are policy, and policy lives in
``services/sync.py``. This module answers only "what did the bank say".
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.security.redact import sanitize

# What a provider failure means, in the only vocabulary sync needs to route on.
# The distinction is load-bearing: ``auth`` earns the user a "Reconnect" button
# and ``payment`` must not, because reconnecting a lapsed subscription fails
# identically and the button would be a lie (ADR-0028).
PROVIDER_ERROR_KINDS = ("auth", "payment", "transient", "malformed")


class ProviderError(Exception):
    """A provider call failed, classified for the caller.

    **The message is sanitized in the constructor, and that is the point.** The
    obvious failure path here is ``except httpx.HTTPStatusError as exc:
    raise ProviderError(str(exc))`` — and ``HTTPStatusError`` stringifies the
    request it failed on, credentials included. A caller who passes ``str(exc)``
    or ``repr(exc)`` therefore *cannot* leak the access URL, because the value
    never survives construction. Pass ``secrets=`` when the caller knows them so
    the literal URL is removed even if it appears in a shape no pattern catches.

    ``secrets`` is used and discarded — never stored on the instance, because an
    exception is exactly the object most likely to be logged, serialized, or
    handed to an error tracker, and keeping the credential on it would recreate
    the problem this class exists to prevent.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "transient",
        status: int | None = None,
        secrets: Iterable[str] = (),
    ) -> None:
        if kind not in PROVIDER_ERROR_KINDS:
            raise ValueError(f"unknown provider error kind: {kind!r}")
        safe = sanitize(message, secrets=secrets)
        super().__init__(safe)
        self.message = safe
        self.kind = kind
        #: The HTTP status when the failure had one, for ``sync_runs.http_status``.
        self.status = status


@dataclass(frozen=True, slots=True)
class ProviderTransaction:
    """One transaction as the bank reported it.

    ``posted_at is None`` **is** the pending signal, and it is the only one. The
    wire format has no ``pending`` field — a pending row is one whose ``posted``
    is ``0`` — so deriving it once here stops every caller from re-deriving it
    slightly differently. ``is_pending`` is a property rather than a field for
    the same reason: two representations of one fact can disagree.
    """

    external_id: str
    amount: Decimal  # + = money in, matching Transaction.amount
    transacted_at: datetime
    currency: str
    posted_at: datetime | None = None
    description: str | None = None
    #: The clean merchant name ("John's Fishin Shack") where ``description`` is a
    #: category-ish label ("Fishing bait") and ``memo`` is the raw bank string.
    #: Sync writes ``Transaction.merchant`` from this.
    payee: str | None = None
    memo: str | None = None
    mcc: str | None = None

    @property
    def is_pending(self) -> bool:
        return self.posted_at is None


@dataclass(frozen=True, slots=True)
class ProviderHolding:
    """One position in an investment account.

    Carried because its *presence* is how investment accounts are identified at
    all: SimpleFIN sends no ``type`` or ``subtype``, so "has holdings" is the
    only structural signal available (fixture README, correction 4).
    """

    market_value: Decimal
    currency: str
    symbol: str | None = None
    description: str | None = None
    shares: Decimal | None = None
    cost_basis: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ProviderAccount:
    """One account and its transactions. Balances are as of ``balance_date``."""

    external_id: str
    name: str
    currency: str
    balance: Decimal
    available_balance: Decimal | None = None
    balance_date: date | None = None
    #: The institution, resolved from the connection this account belongs to.
    #: Flattened onto the account because ``external_key_for`` needs it and the
    #: caller has no other reason to know the payload had two levels.
    org_name: str | None = None
    transactions: tuple[ProviderTransaction, ...] = ()
    holdings: tuple[ProviderHolding, ...] = ()

    @property
    def has_holdings(self) -> bool:
        return bool(self.holdings)

    def holdings_total(self) -> Decimal | None:
        """Σ market value, or ``None`` if the holdings aren't all in one currency.

        Returning ``None`` rather than a mixed-currency sum is deliberate: adding
        USD to EUR produces a number that looks like a balance and is not one.
        The caller decides whether to convert (``services/fx.py``) or to fall
        back to the stated balance.
        """
        if not self.holdings:
            return None
        if any(h.currency != self.currency for h in self.holdings):
            return None
        return sum((h.market_value for h in self.holdings), Decimal("0"))


@dataclass(frozen=True, slots=True)
class ProviderConnection:
    """The institution behind a set of accounts, as the payload describes it."""

    conn_id: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    org_url: str | None = None


@dataclass(frozen=True, slots=True)
class FetchStats:
    """Transport telemetry, for ``sync_runs`` rather than for the ledger."""

    http_ms: int
    http_status: int
    bytes_fetched: int


@dataclass(frozen=True, slots=True)
class AccountSet:
    """One fetch's worth of accounts, plus whatever the provider complained about.

    ``errlist`` is deliberately carried rather than raised: the bridge answers a
    too-long date range with **HTTP 200 and a warning**, so a payload can be
    simultaneously successful and objectionable. Raising would discard three
    perfectly good accounts because one of them was stale, and swallowing would
    report a clean run that wasn't. Sync maps a non-empty ``errlist`` to run
    status ``partial``.
    """

    accounts: tuple[ProviderAccount, ...] = ()
    connections: tuple[ProviderConnection, ...] = ()
    errlist: tuple[str, ...] = ()
    stats: FetchStats | None = None

    @property
    def org_name(self) -> str | None:
        """The institution for the whole set, for ``AccountConnection.org_name``."""
        return self.connections[0].org_name if self.connections else None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """A successful claim: the credential-bearing URL, and nothing else.

    The access URL is a secret and this object exists to move it from the
    provider to ``SecretBox`` in as few frames as possible. It has no ``repr``
    suppression (a frozen dataclass's default repr would print the URL), so it
    must never be logged, formatted into a message, or bound to a structlog
    context — ``__repr__`` is overridden below to make that mistake a non-event.
    """

    access_url: str = field(repr=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load-bearing
        return "ClaimResult(access_url=[redacted])"


@runtime_checkable
class AggregatorProvider(Protocol):
    """What ``services/sync.py`` is allowed to assume about a bank.

    Both methods are ``async`` because the worker is a single asyncio task and
    httpx is the only HTTP client in the backend; a synchronous provider would
    have to be run in a thread to avoid stalling the scheduler.
    """

    name: str

    async def claim(self, setup_token: str) -> ClaimResult:
        """Exchange a setup token for the long-lived access URL."""
        ...

    async def fetch_accounts(self, access_url: str, *, start: datetime) -> AccountSet:
        """Fetch every account, with transactions from ``start`` onward.

        ``start`` is a low-water mark the caller computed, not a fixed window —
        see ``services/sync.py`` for how it is derived from unsettled rows.
        """
        ...


#: ``Account.external_key`` is ``String(300)``. The ceiling here leaves headroom
#: rather than sitting exactly at the limit, so a future widening of the column
#: does not silently change which keys get hashed.
MAX_EXTERNAL_KEY_CHARS = 280
EXTERNAL_KEY_DIGEST_CHARS = 16


def _norm_part(value: str | None) -> str:
    """Casefold and collapse whitespace. Deliberately no more than that.

    Stripping punctuation or digits would raise the match rate on a reconnect at
    the cost of merging accounts that are genuinely distinct ("Card 1234" and
    "Card 5678"), and a wrong merge silently attributes one account's spending
    to another. A missed match only creates a duplicate the user can delete.
    """
    return " ".join((value or "").split()).casefold()


def external_key_for(account: ProviderAccount) -> str:
    """ADR-0009's reconnect key: institution + normalised account name.

    It excludes the provider's account id, the connection id and the balance —
    which is the whole design, because those are precisely what a re-claim
    changes. Reconnect is a remove + re-add (ADR-0009), so a key that included
    the account id would fail to match after the re-claim that it exists to
    survive, and every account would be duplicated.

    The result always fits ``Account.external_key``. Names are short in practice,
    but "in practice" is not a bound: an over-long key would raise at insert,
    failing a sync over a bank with a verbose name. Over the limit, the key is
    truncated and given a hash suffix — plain truncation alone would silently
    merge two accounts that share a long prefix, which is the wrong failure.
    """
    key = f"{_norm_part(account.org_name)}:{_norm_part(account.name)}"
    if len(key) <= MAX_EXTERNAL_KEY_CHARS:
        return key
    digest = hashlib.sha256(key.encode()).hexdigest()[:EXTERNAL_KEY_DIGEST_CHARS]
    return f"{key[: MAX_EXTERNAL_KEY_CHARS - len(digest) - 1]}:{digest}"


# Name fragments, checked in order. SimpleFIN sends no account type at all, so
# this is a guess — and it is always correctable by hand through
# ``PATCH /accounts/{id}``, which is what makes guessing acceptable here. The
# order matters: "Investment Checking" is an investment account that happens to
# say "checking", so the investment words are tested first.
_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("invest", "investment"),
    ("brokerage", "investment"),
    ("retirement", "investment"),
    ("401k", "investment"),
    ("ira", "investment"),
    ("credit", "credit"),
    ("card", "credit"),
    ("loan", "loan"),
    ("mortgage", "loan"),
    ("savings", "depository"),
    ("checking", "depository"),
)


def infer_account_type(account: ProviderAccount) -> str:
    """Guess an ``Account.type`` for a synced account.

    Holdings win over the name, because holdings are *structure* and the name is
    a string a bank chose. "SimpleFIN Savings" in the committed capture holds
    550 shares of AAPL, and calling that a savings account would put a brokerage
    balance under the depository filter forever.
    """
    if account.has_holdings:
        return "investment"
    lowered = account.name.casefold()
    for fragment, kind in _TYPE_HINTS:
        if fragment in lowered:
            return kind
    return "other"


def get_provider(name: str) -> AggregatorProvider:
    """Resolve a provider from the name stored on ``AccountConnection.provider``.

    Selection is **per connection, not global**. A connection row is
    ``simplefin`` or ``fake`` for its whole life and the worker dispatches on the
    row, so a seeded demo connection keeps working under any environment instead
    of breaking the moment the app is deployed with real credentials. Only the
    *claim* endpoint has to guess, and ``METALMARK_SIMPLEFIN_PROVIDER`` is what
    it guesses with.

    The imports are deferred so this module — the seam everything imports —
    stays free of httpx and of the fakes. It also keeps the "may we use the
    fake?" decision at call time rather than import time, when settings may not
    be loaded yet.

    The fake is *refused* outside test/dev rather than warned about. The reason
    is not that the fake is dangerous; it is that a connection syncing against
    nothing produces clean, empty, successful-looking runs, and a silent fake is
    indistinguishable from a bank having a quiet day. A ``RuntimeError`` is the
    loudest available signal that this process is misconfigured.
    """
    if name == "simplefin":
        from app.services.simplefin import SimpleFinProvider

        return SimpleFinProvider()
    if name == "fake":
        from app.services.fake_simplefin import (
            FAKE_ALLOWED_ENVS,
            FakeProvider,
            fake_provider_allowed,
        )

        if not fake_provider_allowed():
            raise RuntimeError(
                "the 'fake' provider requires METALMARK_ENV to be one of "
                f"{', '.join(FAKE_ALLOWED_ENVS)}"
            )
        return FakeProvider()
    raise ValueError(f"unknown aggregator provider: {name!r}")


__all__ = [
    "PROVIDER_ERROR_KINDS",
    "AccountSet",
    "AggregatorProvider",
    "ClaimResult",
    "FetchStats",
    "ProviderAccount",
    "ProviderConnection",
    "ProviderError",
    "ProviderHolding",
    "ProviderTransaction",
    "external_key_for",
    "get_provider",
    "infer_account_type",
]
