"""The SimpleFIN wire format and client.

The parser tests run against the **committed capture**, so they validate our
reading of the real protocol rather than of our own assumptions. The client
tests drive ``httpx.MockTransport``, so they are offline and deterministic.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.services.aggregator import ProviderError
from app.services.connections import _claim_status
from app.services.simplefin import (
    MAX_PAYLOAD_BYTES,
    SimpleFinProvider,
    parse_accounts_payload,
)
from tests.fakes import simplefin as scenarios

ACCESS_URL = "https://alice:s3cr3t-pw@bridge.example.com/simplefin/abcdef"
START = datetime(2026, 1, 1, tzinfo=UTC)


# --- parsing the real capture ---------------------------------------------


def test_capture_parses_into_three_accounts() -> None:
    account_set = scenarios.demo()
    assert [a.name for a in account_set.accounts] == [
        scenarios.DEMO_SAVINGS,
        scenarios.DEMO_CHECKING,
        scenarios.DEMO_EMPTY,
    ]
    assert account_set.errlist == ()
    assert account_set.connections[0].org_name == "SimpleFIN Bridge"


def test_amounts_are_exact_decimals_never_floats() -> None:
    savings = scenarios.account_named(scenarios.demo(), scenarios.DEMO_SAVINGS)
    assert isinstance(savings.balance, Decimal)
    assert savings.balance == Decimal("114685.51")
    assert savings.transactions[0].amount == Decimal("-120.00")
    # A float round-trip is how a cent disappears; assert the type, not just
    # the value, because Decimal("-120.00") == -120.0 is also True.
    assert not isinstance(savings.transactions[0].amount, float)


def test_instants_are_aware_utc() -> None:
    savings = scenarios.account_named(scenarios.demo(), scenarios.DEMO_SAVINGS)
    txn = savings.transactions[0]
    assert txn.transacted_at.tzinfo is not None
    assert txn.posted_at is not None
    assert txn.posted_at.tzinfo is not None
    assert savings.balance_date is not None


def test_payee_description_and_memo_are_distinct_fields() -> None:
    # The correction that changed the design: the published spec's field list
    # omits these, and they are present under version=2. `payee` is the merchant.
    txn = scenarios.account_named(scenarios.demo(), scenarios.DEMO_SAVINGS).transactions[0]
    assert txn.payee == "John's Fishin Shack"
    assert txn.description == "Fishing bait"
    assert txn.memo == "JOHNS FISHIN SHACK BAIT"
    assert txn.mcc == "5812"


def test_transaction_ids_repeat_across_accounts() -> None:
    """The capture's trap, asserted so it cannot be forgotten.

    Ids are unique *within* an account and identical *across* accounts, so
    nothing may look a transaction up by ``external_id`` alone — it must always
    be scoped to ``account_id``, which is what the ``(account_id,
    external_id)`` unique index enforces.
    """
    account_set = scenarios.demo()
    savings = scenarios.account_named(account_set, scenarios.DEMO_SAVINGS)
    checking = scenarios.account_named(account_set, scenarios.DEMO_CHECKING)
    assert {t.external_id for t in savings.transactions} == {
        t.external_id for t in checking.transactions
    }


def test_no_transaction_in_the_capture_is_pending() -> None:
    # Why every pending scenario is constructed: the demo never emits posted==0.
    account_set = scenarios.demo()
    assert not any(
        t.is_pending for a in account_set.accounts for t in a.transactions
    )


def test_a_zero_posted_is_pending_not_1970() -> None:
    payload = {"accounts": [
        {"id": "A", "name": "A", "currency": "USD", "balance": "1.00",
         "transactions": [
             {"id": "t1", "amount": "-1.00", "transacted_at": 1782288000, "posted": 0}
         ]},
    ]}
    txn = parse_accounts_payload(json.dumps(payload).encode()).accounts[0].transactions[0]
    assert txn.is_pending
    assert txn.posted_at is None  # not 1970-01-01


def test_errlist_entries_are_objects_not_strings() -> None:
    # Captured, not assumed: an entry is {"code", "msg"}. Reading it as a bare
    # string would put a dict's repr in the run log.
    account_set = scenarios.with_errlist(
        scenarios.demo(), scenarios.WARN_WINDOW_CAPPED
    )
    assert account_set.errlist == (scenarios.WARN_WINDOW_CAPPED,)


def test_errlist_objects_are_flattened_to_code_colon_message() -> None:
    payload = {
        "accounts": [],
        "errlist": [{"code": "gen.api", "msg": "capped"}],
    }
    account_set = parse_accounts_payload(json.dumps(payload).encode())
    assert account_set.errlist == ("gen.api: capped",)


def test_a_non_list_accounts_field_is_malformed() -> None:
    with pytest.raises(ProviderError) as caught:
        parse_accounts_payload(b'{"accounts": {"nope": 1}}')
    assert caught.value.kind == "malformed"


def test_unparseable_json_is_malformed_and_says_nothing_about_the_body() -> None:
    with pytest.raises(ProviderError) as caught:
        parse_accounts_payload(b"<html>not json at all</html>")
    assert caught.value.kind == "malformed"
    assert "not json at all" not in caught.value.message


def test_a_malformed_row_is_skipped_and_recorded_without_its_contents() -> None:
    """One bad transaction costs a `partial` badge, not the whole run."""
    payload = {"accounts": [
        {"id": "A", "name": "A", "currency": "USD", "balance": "1.00",
         "transactions": [
             {"id": "good", "amount": "-1.00", "transacted_at": 1782288000, "posted": 1782288000},
             {"id": "bad", "amount": "not-a-number", "transacted_at": 1782288000},
             {"amount": "-2.00", "transacted_at": 1782288000},
         ]},
    ]}
    account_set = parse_accounts_payload(json.dumps(payload).encode())
    assert [t.external_id for t in account_set.accounts[0].transactions] == ["good"]
    assert len(account_set.errlist) == 2
    assert all(e.startswith("app.parse:") for e in account_set.errlist)
    # The offending *value* is bank data and must not appear.
    assert not any("not-a-number" in e for e in account_set.errlist)


# --- the HTTP client -------------------------------------------------------


def _provider(handler) -> SimpleFinProvider:
    return SimpleFinProvider(transport=httpx.MockTransport(handler))


def _capture() -> bytes:
    from app.services.fake_simplefin import CAPTURE_PATH

    return CAPTURE_PATH.read_bytes()


async def test_fetch_sends_the_window_and_version_as_query_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_capture())

    account_set = await _provider(handler).fetch_accounts(ACCESS_URL, start=START)

    assert len(seen) == 1
    request = seen[0]
    assert request.url.params["version"] == "2"
    assert request.url.params["pending"] == "1"
    assert request.url.params["start-date"] == str(int(START.timestamp()))
    assert request.url.path.endswith("/accounts")
    assert len(account_set.accounts) == 3
    assert account_set.stats is not None
    assert account_set.stats.http_status == 200
    assert account_set.stats.bytes_fetched == len(_capture())


async def test_a_redirect_is_not_followed() -> None:
    """Non-negotiable: following would re-send the Basic credentials elsewhere."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.invalid/steal"})

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).fetch_accounts(ACCESS_URL, start=START)

    assert len(seen) == 1  # it did not chase the redirect
    assert caught.value.kind == "transient"
    assert "redirect" in caught.value.message.lower()
    assert "attacker.invalid" not in caught.value.message


@pytest.mark.parametrize(
    ("status", "kind"),
    [(403, "auth"), (402, "payment"), (500, "transient"), (400, "transient"), (404, "transient")],
)
async def test_fetch_status_maps_to_a_kind(status: int, kind: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"nope")

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).fetch_accounts(ACCESS_URL, start=START)
    assert caught.value.kind == kind
    assert caught.value.status == status


async def test_402_is_payment_and_never_auth() -> None:
    """A lapsed subscription is not fixed by reconnecting.

    If this mapped to ``auth`` the UI would offer a Reconnect button that fails
    identically, which is worse than no button.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, content=b"")

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).fetch_accounts(ACCESS_URL, start=START)
    assert caught.value.kind == "payment"


async def test_neither_the_status_message_nor_the_url_carries_the_credential() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).fetch_accounts(ACCESS_URL, start=START)
    assert "s3cr3t-pw" not in str(caught.value)
    assert "s3cr3t-pw" not in caught.value.message
    assert "s3cr3t-pw" not in repr(caught.value)


async def test_an_oversized_body_is_refused_before_it_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_PAYLOAD_BYTES + 1))

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).fetch_accounts(ACCESS_URL, start=START)
    assert caught.value.kind == "malformed"


async def test_a_timeout_is_transient_and_does_not_name_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).fetch_accounts(ACCESS_URL, start=START)
    assert caught.value.kind == "transient"
    assert "s3cr3t-pw" not in caught.value.message


# --- claim -----------------------------------------------------------------


def _token(claim_url: str) -> str:
    return base64.b64encode(claim_url.encode()).decode()


async def test_claim_posts_the_decoded_claim_url_and_returns_the_access_url() -> None:
    seen: list[httpx.Request] = []
    claim_url = "https://bridge.example.com/simplefin/claim/DEMO-ABC"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=ACCESS_URL)

    result = await _provider(handler).claim(_token(claim_url))

    assert result.access_url == ACCESS_URL
    assert seen[0].method == "POST"
    assert str(seen[0].url) == claim_url
    assert seen[0].content == b""


async def test_claim_result_never_reveals_the_access_url_in_its_repr() -> None:
    # This object is held for as few frames as possible, but it will end up in
    # a traceback or a debugger eventually. Its repr must be inert.
    from app.services.aggregator import ClaimResult

    assert "s3cr3t-pw" not in repr(ClaimResult(access_url=ACCESS_URL))


async def test_claim_of_a_used_token_is_auth_and_says_to_get_a_new_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).claim(_token("https://bridge.example.com/claim/x"))
    assert caught.value.kind == "auth"
    assert "new one" in caught.value.message


async def test_claim_of_a_token_naming_the_old_bridge_says_so() -> None:
    """A redirect is not a transient failure, and reporting it as one is a trap.

    The bridge has moved hosts, and its old address answers *every* path —
    including a claim — with a 302 to the new host's **root**. So following it
    would post to a homepage; the token is simply from the old place and another
    one is the only fix. Left unclassified it surfaces as a bare "HTTP 302", which
    the API turns into a 502 — advice to retry a request that cannot ever succeed.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://beta.example.com/"})

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).claim(_token("https://bridge.example.com/claim/x"))

    assert caught.value.kind == "auth"
    assert "moved" in caught.value.message
    assert "new one" in caught.value.message  # the same advice a spent token gets
    assert "bridge.example.com" not in repr(caught.value)
    assert [str(r.url) for r in seen] == ["https://bridge.example.com/claim/x"]


def test_a_moved_bridge_is_a_bad_request_and_not_a_bad_gateway() -> None:
    """The other half of the contract above, which lives here because it is the
    same decision seen from the API's side: ``_claim_status`` is what turns a
    provider's status into the one a client acts on.

    A 502 says "the server is briefly broken, try again". For a token naming an
    address the bridge no longer serves, trying again is the one thing that
    cannot work — so it is a 400, in the bucket with a spent token.
    """
    moved = ProviderError("the bridge has moved", kind="auth", status=302)
    spent = ProviderError("already claimed", kind="auth", status=403)
    upstream = ProviderError("boom", kind="transient", status=503)

    assert _claim_status(moved) == 400
    assert _claim_status(spent) == 400
    assert _claim_status(upstream) == 502


async def test_claim_accepts_a_url_safe_token() -> None:
    """The other real way a token arrives, and it fails *silently* if mishandled.

    A URL-safe token read with the standard table does not raise: ``-`` and ``_``
    are base64 characters too, so it decodes to valid-looking garbage. The only
    defence is trying both alphabets, which is what ``_decode_setup_token`` does.

    The URL is not arbitrary — its base64 genuinely ends in ``+`` (standard) and
    ``-`` (url-safe), which is asserted below so this test cannot quietly stop
    covering the alternate alphabet if someone edits the string.
    """
    claim_url = "https://bridge.example.com/simplefin/claim/DEMO-10~"
    standard = base64.b64encode(claim_url.encode()).decode()
    url_safe = base64.urlsafe_b64encode(claim_url.encode()).decode()
    assert standard.endswith("+") and url_safe.endswith("-")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=ACCESS_URL)

    await _provider(handler).claim(url_safe)
    assert str(seen[0].url) == claim_url


async def test_claim_accepts_a_token_that_lost_its_padding() -> None:
    """Tokens get copied off a web page by hand; a missing ``=`` is a typo."""
    claim_url = "https://bridge.example.com/simplefin/claim/DEMO-10"
    padded = base64.b64encode(claim_url.encode()).decode()
    assert padded.endswith("=")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=ACCESS_URL)

    await _provider(handler).claim(padded.rstrip("="))
    assert str(seen[0].url) == claim_url


async def test_claim_rejects_a_token_that_is_not_base64_of_a_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the network")

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).claim("!!! not base64 !!!")
    assert caught.value.kind == "auth"


async def test_claim_does_not_leak_the_setup_token_in_its_error() -> None:
    token = _token("https://bridge.example.com/simplefin/claim/DEMO-SECRET")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"")

    with pytest.raises(ProviderError) as caught:
        await _provider(handler).claim(token)
    assert "DEMO-SECRET" not in str(caught.value)
    assert token not in str(caught.value)
