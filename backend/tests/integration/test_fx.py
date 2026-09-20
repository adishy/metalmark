"""The batched rate source — ``fx.converter()`` and the answers it gives.

``fx_rates`` is **global** (no ``household_id``; the unique key is
``(base_currency, quote_currency, rate_date)``), so a rate written by one test is
visible to every other. The currency codes here — AUD, CAD, NZD — are deliberately
ones no other test and no seed writes, for the reason ``test_ledger.py`` gives at
its own GBP case: a test that assumed it owned a pair would pass or fail depending
on collection order.

What is being pinned is **not** that the two forms of conversion agree. It is that
the batched one gives the same *answers*, on the four shapes the resolution order
can take, with the expected value written out for each — an equivalence test alone
would pass if both forms returned ``None`` for everything.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.db import scoped_session
from app.services import fx, ledger

pytestmark = pytest.mark.integration

D = Decimal

# `until` bounds every load below; the rates themselves straddle the dates asked
# about so the carry-forward rule is exercised rather than assumed.
EARLY = date(2026, 1, 1)
LATE = date(2026, 3, 1)


async def _rates(session, hh, *rows):
    for base, quote, on, rate in rows:
        await ledger.upsert_fx_rate(
            session, hh, base_ccy=base, quote_ccy=quote, rate_date=on, rate=D(rate)
        )


async def _table(session, hh):
    """One rate per shape, and each one written in the direction that makes the
    shape the *only* way to answer:

    ``to_base`` converts into the base, so the two shapes it can meet are the row
    written base-ward (``CAD→USD``, used **direct**) and the row written the other
    way (``AUD→USD`` reached by **inverting** ``USD→AUD``). Both are here, at two
    dates for AUD so the carry-forward has something to carry.

    **Triangulation is deliberately absent.** The order's third branch fires only
    when the target is not the base currency, and ``Converter.to_base`` always
    converts into the base — so it cannot be reached from here, and a test that
    claimed to cover it would be covering the inverse branch under another name.
    It stays reachable in the session path, where ``investments.convert_between``
    converts into an arbitrary currency.
    """
    await _rates(
        session,
        hh,
        ("USD", "AUD", EARLY, "1.40"),
        ("USD", "AUD", LATE, "1.50"),
        ("CAD", "USD", EARLY, "0.74"),
    )


async def test_every_shape_of_lookup_gives_the_rate_the_order_says(household_factory):
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await _table(s, hh)
        conv = await fx.converter(
            s, base_ccy="USD", currencies={"AUD", "CAD"}, until=LATE
        )

        # direct: 100 CAD at the stored 0.74 USD per CAD.
        direct, _ = await conv.to_base(
            amount=D("100"), currency="CAD", on=EARLY, base_ccy="USD"
        )
        # inverse: 150 AUD against 1 USD = 1.50 AUD, so 150 ÷ 1.50 = exactly 100.
        inverse, _ = await conv.to_base(
            amount=D("150"), currency="AUD", on=LATE, base_ccy="USD"
        )
        # inverse again, at a date *between* the pair's two rates: January's.
        carried, _ = await conv.to_base(
            amount=D("100"), currency="AUD", on=date(2026, 2, 15), base_ccy="USD"
        )
        # identity: the base currency is not a lookup at all.
        same, on_same = await conv.to_base(
            amount=D("42"), currency="USD", on=EARLY, base_ccy="USD"
        )
        # and the flag, never a zero (ADR-0017).
        missing = await conv.to_base(
            amount=D("100"), currency="NZD", on=LATE, base_ccy="USD"
        )

    assert direct == D("74.0000")
    assert inverse == D("100.0000")
    assert carried == D("71.4286")  # 100 ÷ 1.40
    assert same == D("42")
    assert on_same == EARLY
    assert missing == (None, None)


async def test_the_batched_answers_are_the_session_answers(household_factory):
    """Every currency, at dates before, between and after its rates.

    The risk this covers is not arithmetic — it is that a second rate source is a
    second place for the resolution order to live. The order is one function
    (``_multiplier``) asked of two sources, and this is what says so.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await _table(s, hh)
        conv = await fx.converter(
            s, base_ccy="USD", currencies={"AUD", "CAD", "SEK"}, until=LATE
        )

        answers = 0
        for currency in ("AUD", "CAD", "SEK", "USD", "NZD"):
            for on in (date(2025, 12, 31), EARLY, date(2026, 2, 15), LATE, date(2026, 4, 1)):
                for amount in (D("100"), D("-123.45")):
                    batched = await conv.to_base(
                        amount=amount, currency=currency, on=on, base_ccy="USD"
                    )
                    one_at_a_time = await fx.to_base(
                        s, amount=amount, currency=currency, on=on, base_ccy="USD"
                    )
                    assert batched == one_at_a_time, (currency, on, amount)
                    answers += batched[0] is not None
        # A matrix where nothing ever resolved would satisfy every assertion above.
        assert answers >= 12


async def test_a_rate_after_the_bound_cannot_be_seen(household_factory):
    """``until`` is which dates may be asked about, not a soft preference.

    The March rate is not merely unused at a February date — it is not loaded, so a
    chart whose last point is in February is drawn from February's information. The
    carry-forward still applies underneath it, which is why the answer at a March
    date under a February bound is January's rate: a caller that asks past its own
    bound gets a stale rate rather than an error, and that is the caller's bug —
    which is the reason the parameter is named for the dates and not for a limit.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await _table(s, hh)
        bounded = await fx.converter(
            s, base_ccy="USD", currencies={"AUD"}, until=date(2026, 2, 1)
        )
        unbounded = await fx.converter(s, base_ccy="USD", currencies={"AUD"}, until=LATE)

        before, _ = await bounded.to_base(
            amount=D("100"), currency="AUD", on=date(2026, 2, 15), base_ccy="USD"
        )
        after, _ = await bounded.to_base(
            amount=D("100"), currency="AUD", on=LATE, base_ccy="USD"
        )
        later, _ = await unbounded.to_base(
            amount=D("100"), currency="AUD", on=LATE, base_ccy="USD"
        )

    assert before == D("71.4286")  # 100 ÷ 1.40, January's rate carried forward
    assert after == before  # the bound hides March even from March
    assert later == D("66.6667")  # and without it, March's rate is there


async def test_a_date_before_every_rate_has_no_answer(household_factory):
    """The "no rate" rule, which is a flag and never a zero (ADR-0017)."""
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await _table(s, hh)
        conv = await fx.converter(s, base_ccy="USD", currencies={"AUD", "SEK"}, until=LATE)

        assert await conv.latest("USD", "AUD", date(2025, 12, 31)) is None
        assert await conv.to_base(
            amount=D("100"), currency="AUD", on=date(2025, 12, 31), base_ccy="USD"
        ) == (None, None)
        # A currency with no rows at all, not merely none early enough.
        assert await conv.latest("NZD", "USD", LATE) is None


async def test_the_currency_set_cannot_lose_an_answer_to_base(household_factory):
    """``currencies`` is a size hint, not a precondition — and this is the proof.

    The load keeps any pair touching a named currency, so it is an
    over-approximation of what a caller could ask for; naming nothing at all still
    loads every pair that ends at the base. That is not luck: ``to_base`` only ever
    asks about ``(from, base)``, and the base is in the set by construction — so no
    filter built from this parameter can drop a row an answer depends on. Naming
    the right currencies makes the load smaller and nothing else, which is why
    naming too many is harmless.
    """
    hh = await household_factory(base="USD")
    async with scoped_session(household_id=hh) as s:
        await _table(s, hh)
        minimal = await fx.converter(s, base_ccy="USD", currencies=set(), until=LATE)

        for currency, amount in (("AUD", D("100")), ("CAD", D("100")), ("NZD", D("100"))):
            assert await minimal.to_base(
                amount=amount, currency=currency, on=EARLY, base_ccy="USD"
            ) == await fx.to_base(
                s, amount=amount, currency=currency, on=EARLY, base_ccy="USD"
            )
