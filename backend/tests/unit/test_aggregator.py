"""The provider seam: normalisation, the reconnect key, and provider selection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.aggregator import (
    AccountSet,
    ProviderAccount,
    ProviderError,
    ProviderHolding,
    ProviderTransaction,
    external_key_for,
    get_provider,
    infer_account_type,
)
from app.services.fake_simplefin import (
    FAKE_ACCESS_URL,
    FakeProvider,
    fake_provider_allowed,
    load_demo_capture,
)
from app.services.simplefin import SimpleFinProvider
from app.settings import get_settings
from tests.fakes import simplefin as scenarios


def _account(name: str, **kwargs) -> ProviderAccount:
    base = {
        "external_id": "ext-1",
        "name": name,
        "currency": "USD",
        "balance": Decimal("10.00"),
        "org_name": "Bridge Bank",
    }
    return ProviderAccount(**{**base, **kwargs})


# --- the reconnect key -----------------------------------------------------


def test_external_key_is_stable_when_the_provider_re_mints_every_id() -> None:
    """ADR-0009's whole point: a reconnect must not duplicate every account.

    Reconnect is a remove + re-add, so the account id, the connection id and the
    balance all change. The key is deliberately built from none of them.
    """
    before = _account("SimpleFIN Savings")
    after = ProviderAccount(
        external_id="completely-different",
        name="SimpleFIN Savings",
        currency="USD",
        balance=Decimal("999999.99"),
        org_name="Bridge Bank",
    )
    assert external_key_for(before) == external_key_for(after)


def test_external_key_distinguishes_different_accounts() -> None:
    assert external_key_for(_account("Checking")) != external_key_for(_account("Savings"))


def test_external_key_distinguishes_the_same_name_at_different_institutions() -> None:
    # Two banks each have a "Checking"; merging them would attribute one bank's
    # spending to the other.
    assert external_key_for(_account("Checking", org_name="Bank A")) != external_key_for(
        _account("Checking", org_name="Bank B")
    )


def test_external_key_ignores_case_and_whitespace() -> None:
    assert external_key_for(_account("  simplefin   savings ")) == external_key_for(
        _account("SimpleFIN Savings")
    )


def test_external_key_does_not_strip_digits() -> None:
    # "Card 1234" and "Card 5678" are genuinely different accounts; normalising
    # harder would raise the reconnect hit rate at the cost of a silent merge.
    assert external_key_for(_account("Card 1234")) != external_key_for(_account("Card 5678"))


def test_external_key_fits_the_column() -> None:
    # Account.external_key is String(300) and raises rather than truncating, so
    # an over-long key would fail a sync over nothing but a verbose bank name.
    key = external_key_for(_account("x" * 200, org_name="y" * 200))
    assert len(key) <= 300


def test_external_key_stays_distinct_when_it_has_to_be_shortened() -> None:
    # Plain truncation would merge two accounts sharing a long prefix — a silent
    # mis-attribution. The hash suffix is what keeps them apart.
    a = external_key_for(_account("x" * 200 + "a", org_name="y" * 200))
    b = external_key_for(_account("x" * 200 + "b", org_name="y" * 200))
    assert a != b
    assert len(a) <= 300
    assert len(b) <= 300


def test_external_key_is_deterministic_when_shortened() -> None:
    # It is compared against a stored value on every subsequent sync, so a
    # non-deterministic shortening would re-key the account every run.
    account = _account("x" * 200, org_name="y" * 200)
    assert external_key_for(account) == external_key_for(account)


# --- type inference --------------------------------------------------------


def test_holdings_make_an_account_an_investment_whatever_its_name_says() -> None:
    # The capture's "SimpleFIN Savings" holds 550 shares of AAPL. Trusting the
    # name would file a brokerage balance under the depository filter forever.
    account = _account(
        "SimpleFIN Savings",
        holdings=(ProviderHolding(market_value=Decimal("105884.8"), currency="USD"),),
    )
    assert infer_account_type(account) == "investment"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Everyday Checking", "depository"),
        ("Rainy Day Savings", "depository"),
        ("Visa Signature Card", "credit"),
        ("Store Credit Line", "credit"),
        ("Auto Loan", "loan"),
        ("Home Mortgage", "loan"),
        ("SimpleFIN Empty Account", "other"),
    ],
)
def test_type_inference_by_name(name: str, expected: str) -> None:
    assert infer_account_type(_account(name)) == expected


def test_investment_type_words_are_checked_before_depository_ones() -> None:
    # "Investment Checking" is an investment account that happens to say
    # "checking"; the ordering of the hint table is what decides.
    assert infer_account_type(_account("Investment Checking")) == "investment"


# --- holdings arithmetic ---------------------------------------------------


def test_holdings_total_sums_a_single_currency() -> None:
    account = _account(
        "Brokerage",
        holdings=(
            ProviderHolding(market_value=Decimal("100.50"), currency="USD"),
            ProviderHolding(market_value=Decimal("0.25"), currency="USD"),
        ),
    )
    assert account.holdings_total() == Decimal("100.75")


def test_holdings_total_refuses_a_mixed_currency_sum() -> None:
    # Adding USD to EUR yields a number that looks like a balance and is not one.
    account = _account(
        "Brokerage",
        holdings=(
            ProviderHolding(market_value=Decimal("100.00"), currency="USD"),
            ProviderHolding(market_value=Decimal("50.00"), currency="EUR"),
        ),
    )
    assert account.holdings_total() is None


def test_holdings_total_of_no_holdings_is_none_not_zero() -> None:
    assert _account("Checking").holdings_total() is None


# --- pending ---------------------------------------------------------------


def test_pending_is_derived_from_a_missing_posted_at() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    pending = ProviderTransaction(
        external_id="t", amount=Decimal("-1"), transacted_at=moment, currency="USD"
    )
    posted = ProviderTransaction(
        external_id="t",
        amount=Decimal("-1"),
        transacted_at=moment,
        currency="USD",
        posted_at=moment,
    )
    assert pending.is_pending and not posted.is_pending


# --- provider selection ----------------------------------------------------


def test_get_provider_returns_the_real_provider_by_name() -> None:
    assert isinstance(get_provider("simplefin"), SimpleFinProvider)


def test_get_provider_returns_the_fake_in_dev_and_test() -> None:
    assert fake_provider_allowed()
    assert isinstance(get_provider("fake"), FakeProvider)


def test_get_provider_refuses_the_fake_outside_dev_and_test(monkeypatch) -> None:
    """A connection syncing against nothing looks exactly like a quiet day.

    The refusal is deliberately loud: a silent fake produces clean, empty,
    successful runs, which is indistinguishable from a bank with no activity.
    """
    monkeypatch.setenv("METALMARK_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="METALMARK_ENV"):
            get_provider("fake")
        # The real provider is still fine — only the fake is gated.
        assert isinstance(get_provider("simplefin"), SimpleFinProvider)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_get_provider_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown aggregator provider"):
        get_provider("plaid")


# --- the fake --------------------------------------------------------------


async def test_fake_pops_one_script_entry_per_fetch() -> None:
    first, second = scenarios.pending_then_posted_same_id()
    provider = FakeProvider(script=[first, second])
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    assert (await provider.fetch_accounts("u", start=moment)) is first
    assert (await provider.fetch_accounts("u", start=moment)) is second


async def test_fake_serves_empty_sets_once_the_script_runs_out() -> None:
    # A test that runs out of script should fail visibly rather than silently
    # receive the previous entry again.
    provider = FakeProvider(script=[scenarios.demo()])
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    await provider.fetch_accounts("u", start=moment)
    exhausted = await provider.fetch_accounts("u", start=moment)
    assert exhausted.accounts == ()


async def test_fake_can_repeat_its_last_entry_instead() -> None:
    provider = FakeProvider(script=[scenarios.demo()], repeat_last=True)
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    await provider.fetch_accounts("u", start=moment)
    again = await provider.fetch_accounts("u", start=moment)
    assert len(again.accounts) == 3


async def test_fake_records_every_credential_it_was_handed() -> None:
    """The mechanism behind the no-credential-in-any-log test.

    The fake holds the exact access URL so a later assertion can search every
    log record, run event and error string for it. A fake that swallowed its
    input would make that test unwritable.
    """
    provider = FakeProvider(script=[scenarios.demo()])
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    await provider.fetch_accounts(FAKE_ACCESS_URL, start=moment)
    await provider.claim("setup-token-abc")

    assert provider.seen_access_urls == [FAKE_ACCESS_URL]
    assert provider.seen_setup_tokens == ["setup-token-abc"]

    provider.reset()
    assert provider.seen_access_urls == [] and provider.fetch_count == 0


async def test_fake_raises_the_scripted_error_then_can_recover() -> None:
    # Mutable on purpose: "failed, then recovered" is what the
    # notify-on-transition rule needs in order to be tested at all.
    provider = FakeProvider(
        script=[scenarios.demo()], raise_on_fetch=ProviderError("boom", kind="auth")
    )
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ProviderError):
        await provider.fetch_accounts(FAKE_ACCESS_URL, start=moment)

    provider.raise_on_fetch = None
    assert len((await provider.fetch_accounts(FAKE_ACCESS_URL, start=moment)).accounts) == 3


def test_the_fake_access_url_is_reserved_and_obviously_not_real() -> None:
    # .invalid can never resolve (RFC 2606), so a misconfiguration cannot send
    # anything anywhere — while still being one exact string to grep logs for.
    assert FAKE_ACCESS_URL.startswith("https://fake-user:fake-password@fake-bridge.invalid/")


def test_load_demo_capture_resolves_the_fixture_from_the_app_package() -> None:
    # The fake ships in app/, the fixture lives under tests/; if the image ever
    # stops shipping tests/ this quietly returns an empty set rather than
    # crashing the worker, so assert it is actually finding the file.
    account_set: AccountSet = load_demo_capture()
    assert len(account_set.accounts) == 3
