"""SimpleFIN Bridge: the wire format, and the HTTP client that fetches it.

Split in two on purpose. ``parse_accounts_payload`` is pure — bytes in, an
``AccountSet`` out — and is used by ``FakeProvider`` and by the unit tests
against the committed capture, so the fixture is read by the *production*
parser. That is the whole reason the fixture is a real capture: a hand-written
parser tested against a hand-written payload proves only that two guesses agree.

``SimpleFinProvider`` is the I/O half: claim a setup token, then fetch accounts
with basic auth.

Protocol notes that cost real time to rediscover (see
``tests/fixtures/simplefin/README.md`` for how each was established):

* ``/info`` advertises ``{"versions": ["1.0"]}`` and ``version=2`` is accepted
  anyway. Do not gate on the advertised list.
* ``version=2`` is what adds ``payee``/``memo``/``mcc``.
* A transaction is pending when ``posted == 0``; there is no ``pending`` field.
* ``errlist`` entries are **objects** — ``{"code": ..., "msg": ...}`` — not
  strings. ``gen.api`` is a complaint about *our request* and arrives on HTTP
  200, so it must never be read as a connection failure.
* Transaction ids are unique within an account but repeat across accounts, so
  nothing downstream may look one up by ``external_id`` alone.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.services.aggregator import (
    AccountSet,
    ClaimResult,
    FetchStats,
    ProviderAccount,
    ProviderConnection,
    ProviderError,
    ProviderHolding,
    ProviderTransaction,
)

#: The bridge caps a pull at 90 days and warns above 45 (fixture README — a
#: capture's own ``errlist`` is where both numbers come from). Sync asks for 45,
#: the bottom of the recommended range, so the warning is not a fixture of every
#: install.
MAX_WINDOW_DAYS = 90
RECOMMENDED_WINDOW_DAYS = 45

#: Sent as ``version``. 2 is what supplies payee/memo/mcc.
API_VERSION = "2"

#: Bound before ``json.loads``, never after. ``bytes_fetched`` is a reported
#: metric and an unbounded body is an unbounded memory write — a hostile or
#: broken bridge should cost a bounded amount of RAM.
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

CLAIM_TIMEOUT_SECONDS = 30.0
FETCH_TIMEOUT_SECONDS = 60.0


def _money(value: Any) -> Decimal:
    """Parse a provider amount without ever passing through a float.

    ``json.loads`` is called with ``parse_float=Decimal`` below, so a JSON
    number arrives already exact; a string arrives as a string. ``str()`` on a
    ``Decimal`` or ``int`` is lossless, which is what makes this safe.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _epoch(value: Any) -> datetime | None:
    """Unix seconds → aware UTC datetime. ``None`` for absent, 0, or junk.

    ``0`` maps to ``None`` deliberately: the protocol uses it as the sentinel
    for "not posted yet", and a datetime of 1970-01-01 would be a far worse
    thing to hand the ledger than an explicit absence.
    """
    if value in (None, 0, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _epoch_date(value: Any) -> date | None:
    moment = _epoch(value)
    return moment.date() if moment else None


def _text(value: Any) -> str | None:
    """A trimmed string, or ``None`` if empty.

    Empty-is-``None`` matters for ``merchant``: sync only writes a provider
    value when it has one, and ``""`` would otherwise look like a real value
    that a rule then has to fight.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _err_text(entry: Any) -> str:
    """Flatten one ``errlist`` entry to ``"code: message"``.

    Entries are objects — this was captured, not assumed. A bare string is
    still accepted because the fallback costs nothing and an unrecognised entry
    must not crash a run that otherwise succeeded.
    """
    if isinstance(entry, dict):
        code = _text(entry.get("code")) or "unknown"
        msg = _text(entry.get("msg")) or _text(entry.get("message")) or "(no message)"
        return f"{code}: {msg}"
    return str(entry)


def parse_accounts_payload(raw: bytes) -> AccountSet:
    """``/accounts`` response body → ``AccountSet``.

    Raises ``ProviderError(kind="malformed")`` when the *payload* is
    structurally wrong, but only ever *skips* a malformed individual row,
    recording it in ``errlist`` as an ``app.parse`` entry. That asymmetry is the
    point: one unparseable transaction should cost the run a `partial` badge,
    not throw away the other three hundred.

    Nothing from a bad row is ever included in the message — only which account
    and which key were wrong. The value that failed to parse is bank data.
    """
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderError(
            f"bridge returned unparseable JSON ({type(exc).__name__})", kind="malformed"
        ) from None

    if not isinstance(payload, dict):
        raise ProviderError(
            f"bridge returned {type(payload).__name__}, expected an object", kind="malformed"
        )

    raw_accounts = payload.get("accounts")
    if raw_accounts is None:
        raw_accounts = []
    if not isinstance(raw_accounts, list):
        raise ProviderError("bridge returned a non-list 'accounts'", kind="malformed")

    connections = tuple(_parse_connection(c) for c in payload.get("connections") or [])
    # conn_id → org_name, so each account can carry its institution without the
    # caller having to join the two levels back together.
    org_by_conn = {c.conn_id: c.org_name for c in connections if c.conn_id}

    errlist = [_err_text(e) for e in payload.get("errlist") or []]
    accounts = []
    for index, raw_account in enumerate(raw_accounts):
        account = _parse_account(raw_account, index, org_by_conn, errlist)
        if account is not None:
            accounts.append(account)

    return AccountSet(
        accounts=tuple(accounts),
        connections=connections,
        errlist=tuple(errlist),
    )


def _parse_connection(raw: Any) -> ProviderConnection:
    if not isinstance(raw, dict):
        return ProviderConnection()
    return ProviderConnection(
        conn_id=_text(raw.get("conn_id")),
        org_id=_text(raw.get("org_id")),
        org_name=_text(raw.get("org_name")),
        org_url=_text(raw.get("org_url")),
    )


def _parse_account(
    raw: Any, index: int, org_by_conn: dict[str, str | None], errlist: list[str]
) -> ProviderAccount | None:
    if not isinstance(raw, dict):
        errlist.append(f"app.parse: account at index {index} is not an object; skipped")
        return None
    external_id = _text(raw.get("id"))
    name = _text(raw.get("name"))
    if not external_id or not name:
        # Name and id are the two fields every downstream step assumes. A
        # nameless account cannot be keyed for reconnect and cannot be shown.
        errlist.append(f"app.parse: account at index {index} has no id or name; skipped")
        return None

    transactions = []
    for txn_index, raw_txn in enumerate(raw.get("transactions") or []):
        txn = _parse_transaction(raw_txn, external_id, txn_index, errlist)
        if txn is not None:
            transactions.append(txn)

    holdings = []
    for raw_holding in raw.get("holdings") or []:
        holding = _parse_holding(raw_holding)
        if holding is not None:
            holdings.append(holding)

    try:
        balance = _money(raw.get("balance") or 0)
    except (InvalidOperation, ValueError, TypeError):
        errlist.append(f"app.parse: account {external_id!r} has an unparseable balance")
        balance = Decimal("0")

    available = None
    if raw.get("available-balance") is not None:
        try:
            available = _money(raw["available-balance"])
        except (InvalidOperation, ValueError, TypeError):
            available = None

    return ProviderAccount(
        external_id=external_id,
        name=name,
        # A missing currency is assumed to be the ledger's base currency by
        # sync; defaulting to "" here would produce an invalid ISO code that
        # only fails later, at insert.
        currency=(_text(raw.get("currency")) or "").upper(),
        balance=balance,
        available_balance=available,
        balance_date=_epoch_date(raw.get("balance-date")),
        org_name=org_by_conn.get(_text(raw.get("conn_id")) or ""),
        transactions=tuple(transactions),
        holdings=tuple(holdings),
    )


def _parse_transaction(
    raw: Any, account_external_id: str, index: int, errlist: list[str]
) -> ProviderTransaction | None:
    if not isinstance(raw, dict):
        errlist.append(
            f"app.parse: transaction {index} of account {account_external_id!r} is not an object"
        )
        return None
    external_id = _text(raw.get("id"))
    if not external_id:
        errlist.append(
            f"app.parse: transaction {index} of account {account_external_id!r} has no id; skipped"
        )
        return None
    try:
        amount = _money(raw.get("amount"))
    except (InvalidOperation, ValueError, TypeError):
        errlist.append(
            f"app.parse: transaction {external_id!r} of account "
            f"{account_external_id!r} has an unparseable amount; skipped"
        )
        return None

    posted_at = _epoch(raw.get("posted"))
    # Fall back to `posted` when `transacted_at` is absent: for a settled row
    # they are usually the same instant, and a missing date would otherwise drop
    # a real transaction out of the ledger entirely.
    transacted_at = _epoch(raw.get("transacted_at")) or posted_at
    if transacted_at is None:
        errlist.append(
            f"app.parse: transaction {external_id!r} of account "
            f"{account_external_id!r} has no date; skipped"
        )
        return None

    return ProviderTransaction(
        external_id=external_id,
        amount=amount,
        transacted_at=transacted_at,
        currency=(_text(raw.get("currency")) or "").upper(),
        posted_at=posted_at,
        description=_text(raw.get("description")),
        payee=_text(raw.get("payee")),
        memo=_text(raw.get("memo")),
        mcc=_text(raw.get("mcc")),
    )


def _parse_holding(raw: Any) -> ProviderHolding | None:
    if not isinstance(raw, dict):
        return None
    try:
        market_value = _money(raw.get("market_value"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    shares = cost_basis = None
    try:
        if raw.get("shares") is not None:
            shares = _money(raw["shares"])
        if raw.get("cost_basis") is not None:
            cost_basis = _money(raw["cost_basis"])
    except (InvalidOperation, ValueError, TypeError):
        pass
    return ProviderHolding(
        market_value=market_value,
        currency=(_text(raw.get("currency")) or "").upper(),
        symbol=_text(raw.get("symbol")),
        description=_text(raw.get("description")),
        shares=shares,
        cost_basis=cost_basis,
    )


def _decode_setup_token(setup_token: str) -> str:
    """A setup token is base64 of the claim URL. Decode it, or say why not.

    Both alphabets are attempted, because the spec does not say which is used
    and the wrong table decodes to *valid-looking garbage* rather than failing —
    ``+`` and ``/`` and ``-`` and ``_`` are all in the base64 alphabet, so no
    error is raised to tell you that you guessed wrong. The translated retry is
    only built when the token actually contains a URL-safe character.

    Padding is restored before decoding: tokens get copied out of a web page by
    hand, and a missing ``=`` is a copying artefact, not a bad credential.

    The ``startswith`` check at the end is what actually decides — a decode that
    succeeds but yields prose is still a rejected token.
    """
    token = setup_token.strip()
    candidates = [token]
    if "-" in token or "_" in token:
        candidates.append(token.translate(str.maketrans("-_", "+/")))

    for candidate in candidates:
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            decoded = base64.b64decode(padded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded.startswith(("http://", "https://")):
            return decoded

    raise ProviderError(
        "setup token is not valid base64 of a claim URL; copy it again from the bridge",
        kind="auth",
    )


class SimpleFinProvider:
    """The backend's only outbound HTTP client.

    ``transport`` exists so tests can drive this offline with
    ``httpx.MockTransport``. Everything else about it is fixed: the client is
    constructed per call, so a failure cannot leave a half-open connection pool
    behind, and ``follow_redirects`` is **off for every request**.

    That last one is a security decision, not a default. This client sends the
    access URL's Basic credentials on every fetch, and httpx follows a redirect
    by re-sending them to whatever host the response names. A single 302 from a
    compromised or mistyped bridge host would hand the credential to that host.
    Not following means a moved endpoint surfaces as an error the user can read
    instead of a credential handed to a stranger.
    """

    name = "simplefin"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
            headers={"User-Agent": "MetalMark/0.1 (+https://metalmark.app)"},
        )

    async def claim(self, setup_token: str) -> ClaimResult:
        """Trade a setup token for the long-lived access URL.

        403 here means the setup token was already used or is unknown — the
        bridge mints each demo token for a single claim. It is a terminal state,
        not a retry: the same token will fail identically forever, so it is
        classified ``auth`` and the UI asks for a new one.
        """
        claim_url = _decode_setup_token(setup_token)
        secrets = (setup_token, claim_url)

        async with self._client(CLAIM_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(claim_url)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    f"claim timed out after {CLAIM_TIMEOUT_SECONDS:.0f}s",
                    kind="transient",
                    secrets=secrets,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"claim failed: {type(exc).__name__}",
                    kind="transient",
                    secrets=secrets,
                ) from exc

        status = response.status_code
        if status == 200:
            access_url = response.text.strip()
            if not access_url.startswith(("http://", "https://")):
                raise ProviderError(
                    "bridge returned a claim response that is not an access URL",
                    kind="malformed",
                    status=status,
                    secrets=secrets,
                )
            return ClaimResult(access_url=access_url)
        if status == 403:
            raise ProviderError(
                "setup token was already claimed or is not recognized; "
                "generate a new one at the bridge",
                kind="auth",
                status=status,
                secrets=secrets,
            )
        if status == 402:
            raise ProviderError(
                "the bridge subscription for this token is not active",
                kind="payment",
                status=status,
                secrets=secrets,
            )
        raise ProviderError(
            f"claim failed with HTTP {status}",
            kind="transient",
            status=status,
            secrets=secrets,
        )

    async def fetch_accounts(self, access_url: str, *, start: datetime) -> AccountSet:
        """Fetch every account with transactions at or after ``start``.

        The URL is built from the *stored* access URL, whose host is whatever
        the bridge's claim response named — never a hard-coded constant, because
        the bridge has already moved hosts once (``bridge`` → ``beta-bridge``).
        """
        start_date = int(start.timestamp())
        secrets = (access_url,)
        url = f"{access_url.rstrip('/')}/accounts"
        params = {"version": API_VERSION, "pending": "1", "start-date": str(start_date)}

        started = time.perf_counter()
        async with self._client(FETCH_TIMEOUT_SECONDS) as client:
            try:
                async with client.stream("GET", url, params=params) as response:
                    status = response.status_code
                    body = await _read_bounded(response)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    f"fetch timed out after {FETCH_TIMEOUT_SECONDS:.0f}s",
                    kind="transient",
                    secrets=secrets,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"fetch failed: {type(exc).__name__}",
                    kind="transient",
                    secrets=secrets,
                ) from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if status != 200:
            raise _fetch_error(status, len(body), secrets)

        account_set = parse_accounts_payload(body)
        return AccountSet(
            accounts=account_set.accounts,
            connections=account_set.connections,
            errlist=account_set.errlist,
            stats=FetchStats(
                http_ms=elapsed_ms, http_status=status, bytes_fetched=len(body)
            ),
        )


async def _read_bounded(response: httpx.Response) -> bytes:
    """Read a response body, refusing to grow past ``MAX_PAYLOAD_BYTES``.

    Checked while streaming rather than afterwards, because checking afterwards
    means the oversized body is already in memory — which is the thing the bound
    exists to prevent.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_PAYLOAD_BYTES:
            raise ProviderError(
                f"bridge response exceeded {MAX_PAYLOAD_BYTES // (1024 * 1024)} MB",
                kind="malformed",
                status=response.status_code,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _fetch_error(status: int, byte_count: int, secrets: tuple[str, ...]) -> ProviderError:
    """Map a non-200 fetch status onto a ``ProviderError`` kind.

    The 402/403 split is the one that matters. **403 is ``auth``** — the access
    URL was revoked, and reconnecting is exactly the fix, so the UI's
    "Reconnect" button tells the truth. **402 is ``payment``** — a lapsed Bridge
    subscription, which reconnecting cannot fix, so offering that button would
    send the user through a flow that fails identically. Everything else is
    ``transient`` and must not touch the connection's health at all: a 5xx is
    our problem, not the connection's.
    """
    if status == 403:
        return ProviderError(
            "access was revoked; reconnect this connection",
            kind="auth",
            status=status,
            secrets=secrets,
        )
    if status == 402:
        return ProviderError(
            "the bridge subscription for this connection is not active",
            kind="payment",
            status=status,
            secrets=secrets,
        )
    if 300 <= status < 400:
        # Not followed, by design. Naming it distinctly turns "the bridge moved"
        # into something a human can act on instead of a bare status code.
        return ProviderError(
            f"bridge redirected (HTTP {status}); it may have moved hosts",
            kind="transient",
            status=status,
            secrets=secrets,
        )
    return ProviderError(
        f"fetch failed with HTTP {status} ({byte_count} bytes)",
        kind="transient",
        status=status,
        secrets=secrets,
    )


__all__ = [
    "API_VERSION",
    "MAX_PAYLOAD_BYTES",
    "MAX_WINDOW_DAYS",
    "RECOMMENDED_WINDOW_DAYS",
    "SimpleFinProvider",
    "parse_accounts_payload",
]
