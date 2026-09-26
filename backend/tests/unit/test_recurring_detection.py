"""The recurring detector's arithmetic (ADR-0053).

``detect`` is a pure function of the household's transactions, so it is tested
as one: no database, no session, and a fixed ``today`` so a test never depends
on when it runs. What the tests pin is the judgement the detector makes — which
groups are patterns, how far a bill may drift before it stops being one, and the
two places it says no on purpose (too few occurrences, and already tracked).

The two "already tracked" cases matter more than they look: a suggestion that a
series already covers is a question the person answered, asked again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.ledger import Transaction
from app.models.recurring import CADENCES, RecurringSeries
from app.services import income as income_svc
from app.services import recurring as svc

pytestmark = pytest.mark.unit

D = Decimal
TODAY = date(2026, 9, 25)
ACCOUNT = uuid.uuid4()


def _txn(day: date, amount: str, *, text: str = "Streaming", merchant: bool = False,
         account_id=ACCOUNT, category_id=None, hidden=False, pending=False):
    """One transaction, as much of it as the detector reads.

    Unpersisted on purpose: the detector never queries, and an ORM object built
    by hand is the only way to state a date exactly. ``id`` is set because a
    suggestion carries the transaction it was picked from (ADR-0053).
    """
    txn = Transaction(
        id=uuid.uuid4(),
        account_id=account_id,
        amount=D(amount),
        currency="USD",
        transacted_at=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        category_id=category_id,
        is_hidden=hidden,
        is_pending=pending,
    )
    # ``merchant`` is what a rule, the bank or the person named; ``description``
    # is the raw string, and a hand-entered row has only the second.
    if merchant:
        txn.merchant = text
        txn.description = None
    else:
        txn.merchant = None
        txn.description = text
    return txn


def _monthly(count: int, amount: str = "-12.99", *, start=date(2026, 1, 5), text="Streaming",
             merchant: bool = False):
    return [
        _txn(date(start.year, start.month + i, start.day), amount, text=text, merchant=merchant)
        if start.month + i <= 12
        else _txn(date(start.year + 1, start.month + i - 12, start.day), amount, text=text,
                  merchant=merchant)
        for i in range(count)
    ]


def _series(*, account_id=ACCOUNT, merchant: str | None = None, amount: str = "-12.99"):
    return RecurringSeries(
        id=uuid.uuid4(),
        household_id=uuid.uuid4(),
        name=merchant or "Anything",
        merchant=merchant,
        account_id=account_id,
        amount=D(amount),
        currency="USD",
        cadence="monthly",
        is_active=True,
    )


# ---- the cadence vocabulary --------------------------------------------------


def test_every_cadence_has_a_multiplier_and_a_nominal_length():
    assert set(svc.CADENCE_ANNUAL_MULTIPLIER) == set(CADENCES)
    assert set(svc.CADENCE_DAYS) == set(CADENCES)


def test_the_shared_cadences_mean_the_same_as_they_do_for_a_paycheque():
    """A "biweekly" bill and a biweekly paycheque are the same 26 a year. The two
    vocabularies are separate constants on purpose (a bill can be semiannual and a
    paycheque cannot), so this is what keeps them from drifting apart."""
    shared = set(income_svc.ANNUAL_MULTIPLIER) & set(svc.CADENCE_ANNUAL_MULTIPLIER)
    assert shared
    for cadence in shared:
        assert svc.CADENCE_ANNUAL_MULTIPLIER[cadence] == income_svc.ANNUAL_MULTIPLIER[cadence]


def test_monthly_amount_is_the_annual_figure_over_twelve():
    assert svc.monthly_amount(D("-120.00"), "annual") == D("-10.00")
    assert svc.monthly_amount(D("-100.00"), "monthly") == D("-100.00")
    # 26/12 of a biweekly charge, to the cent.
    assert svc.monthly_amount(D("-100.00"), "biweekly") == D("-216.67")
    # Weekly is 52/12, not 4x the monthly figure.
    assert svc.monthly_amount(D("-10.00"), "weekly") == D("-43.33")


# ---- what the detector offers ------------------------------------------------


def test_a_monthly_pattern_is_offered_with_its_median_amount_and_cadence():
    rows = _monthly(5)
    out = svc.detect(rows, [], today=TODAY)
    assert len(out) == 1
    suggestion = out[0]
    assert suggestion.cadence == "monthly"
    assert suggestion.amount == D("-12.99")
    assert suggestion.occurrences == 5
    assert suggestion.first_date == date(2026, 1, 5)
    assert suggestion.last_date == date(2026, 5, 5)
    # The next one is due a nominal month after the last, and the suggestion
    # carries the newest charge so accepting it can seed from a real row.
    assert suggestion.next_due_date == date(2026, 6, 4)
    assert suggestion.transaction_id == rows[-1].id
    assert suggestion.currency == "USD"


def test_the_grouping_is_merchant_or_description_and_it_keeps_the_merchant():
    """A bank row carries a merchant and no description; a hand-entered one the
    other way round. Both are "the same bill" to a person, so both are grouped,
    and the suggestion keeps the merchant when there is one to keep."""
    rows = _monthly(3, text="NETFLIX.COM 1234", merchant=True)
    out = svc.detect(rows, [], today=TODAY)
    assert len(out) == 1
    assert out[0].merchant == "NETFLIX.COM 1234"
    assert out[0].name == "NETFLIX.COM 1234"


def test_a_set_of_one_offs_is_not_a_pattern():
    rows = [
        _txn(date(2026, 1, 5), "-12.99", text="Streaming"),
        _txn(date(2026, 1, 9), "-40.00", text="Hardware store"),
        _txn(date(2026, 2, 17), "-8.10", text="Coffee"),
    ]
    assert svc.detect(rows, [], today=TODAY) == []


def test_two_occurrences_are_not_enough_for_a_monthly_series():
    assert svc.detect(_monthly(2), [], today=TODAY) == []


def test_a_yearly_bill_clears_at_two_because_a_third_would_take_three_years():
    rows = [
        _txn(date(2024, 4, 1), "-840.00", text="Home insurance"),
        _txn(date(2025, 4, 1), "-840.00", text="Home insurance"),
    ]
    out = svc.detect(rows, [], today=TODAY)
    assert len(out) == 1
    assert out[0].cadence == "annual"


def test_gaps_that_drift_further_than_the_tolerance_are_not_a_cadence():
    # 30, 30, 30, 88: the median is 30, so it is "monthly" by the nearest-nominal
    # rule, and the spread is what refuses it.
    rows = _monthly(4) + [_txn(date(2026, 8, 1), "-12.99", text="Streaming")]
    assert svc.detect(rows, [], today=TODAY) == []


def test_a_weekly_series_tolerates_a_shifted_day_but_not_a_missed_week():
    on_time = [
        _txn(date(2026, 9, 1), "-9.00", text="Laundry"),
        _txn(date(2026, 9, 8), "-9.00", text="Laundry"),
        _txn(date(2026, 9, 15), "-9.00", text="Laundry"),
        _txn(date(2026, 9, 23), "-9.00", text="Laundry"),  # a day late
    ]
    out = svc.detect(on_time, [], today=TODAY)
    assert [s.cadence for s in out] == ["weekly"]

    with_a_gap = [*on_time[:3], _txn(date(2026, 10, 6), "-9.00", text="Laundry")]
    assert svc.detect(with_a_gap, [], today=TODAY) == []


def test_amounts_that_swing_past_the_tolerance_are_not_one_series():
    rows = [
        _txn(date(2026, 1, 5), "-40.00", text="Power and water"),
        _txn(date(2026, 2, 5), "-42.00", text="Power and water"),
        _txn(date(2026, 3, 5), "-61.00", text="Power and water"),
    ]
    assert svc.detect(rows, [], today=TODAY) == []


def test_a_variable_utility_bill_inside_the_tolerance_is_a_series():
    rows = [
        _txn(date(2026, 1, 5), "-140.00", text="Power and water"),
        _txn(date(2026, 2, 5), "-152.00", text="Power and water"),
        _txn(date(2026, 3, 5), "-148.00", text="Power and water"),
    ]
    out = svc.detect(rows, [], today=TODAY)
    assert [s.cadence for s in out] == ["monthly"]
    # The median, not the mean: a winter spike is not what the series "is".
    assert out[0].amount == D("-148.00")


def test_direction_is_part_of_the_group_so_a_refund_is_not_the_bill():
    rows = [
        _txn(date(2026, 1, 5), "-12.99", text="Streaming"),
        _txn(date(2026, 2, 5), "-12.99", text="Streaming"),
        _txn(date(2026, 3, 5), "-12.99", text="Streaming"),
        _txn(date(2026, 4, 5), "12.99", text="Streaming"),  # a refund
    ]
    out = svc.detect(rows, [], today=TODAY)
    assert len(out) == 1
    assert out[0].occurrences == 3
    assert out[0].amount == D("-12.99")


def test_a_pattern_in_another_account_is_its_own_series():
    other = uuid.uuid4()
    rows = _monthly(3) + [_txn(d, "-12.99", account_id=other) for d in
                          (date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5))]
    out = svc.detect(rows, [], today=TODAY)
    assert {s.account_id for s in out} == {ACCOUNT, other}


def test_hidden_and_pending_rows_are_not_patterns():
    rows = _monthly(3)
    assert svc.detect([_txn(r.transacted_at.date(), "-12.99", hidden=True) for r in rows],
                      [], today=TODAY) == []
    assert svc.detect([_txn(r.transacted_at.date(), "-12.99", pending=True) for r in rows],
                      [], today=TODAY) == []


def test_old_occurrences_outside_the_window_do_not_count():
    rows = _monthly(3, start=date(2022, 1, 5))
    assert svc.detect(rows, [], today=TODAY) == []


def test_suggestions_are_biggest_monthly_first():
    rows = (
        _monthly(3, "-12.99", text="Streaming")
        + _monthly(3, "-1500.00", text="Rent")
        + _monthly(3, "-60.00", text="Gym")
    )
    out = svc.detect(rows, [], today=TODAY)
    assert [s.name for s in out] == ["Rent", "Gym", "Streaming"]


# ---- what the detector refuses to ask twice ----------------------------------


def test_a_suggestion_a_series_already_covers_is_not_offered():
    rows = _monthly(3)
    assert svc.detect(rows, [_series(merchant="Streaming")], today=TODAY) == []


def test_an_inactive_series_still_covers_what_it_tracks():
    """Pausing is "stop counting this in my totals", not "forget it exists" —
    so the detector does not start re-offering a series the person paused."""
    paused = _series(merchant="Streaming")
    paused.is_active = False
    assert svc.detect(_monthly(3), [paused], today=TODAY) == []


def test_a_series_for_another_merchant_does_not_cover_this_pattern():
    rows = _monthly(3)
    assert len(svc.detect(rows, [_series(merchant="Rent")], today=TODAY)) == 1


def test_a_broad_series_covers_the_whole_account():
    """With no merchant, a series is "this account has recurring things" — the
    detector's answer to a person who has already said so is to stop asking."""
    assert svc.detect(_monthly(3), [_series()], today=TODAY) == []


def test_a_series_of_the_opposite_direction_does_not_cover_the_pattern():
    assert len(svc.detect(_monthly(3), [_series(amount="12.99")], today=TODAY)) == 1


# ---- boundaries --------------------------------------------------------------


def test_the_nearest_nominal_gap_wins_but_only_within_the_tolerance():
    assert svc._cadence_for(30) == "monthly"
    assert svc._cadence_for(31) == "monthly"
    assert svc._cadence_for(15) == "semimonthly"
    assert svc._cadence_for(14) == "biweekly"
    # 45 days is nobody's month (30 ± 7.5) and nobody's quarter (91 ± 23).
    assert svc._cadence_for(45) is None


def test_the_window_is_three_years_and_it_decides_which_rows_count():
    assert svc.DETECTION_WINDOW_DAYS == 3 * 365
    # 2023-06-05 is 1308 days before TODAY: outside the window.
    assert svc.detect(
        [_txn(date(2023, m, 5), "-12.99") for m in (4, 5, 6)], [], today=TODAY
    ) == []
    # A run that straddles the edge keeps only the rows inside it — the count a
    # person sees must not depend on how old the household is.
    straddling = (
        [_txn(date(2023, m, 5), "-12.99") for m in (4, 5, 6)]
        + [_txn(date(2023, m, 5), "-12.99") for m in (11, 12)]
        + [_txn(date(2024, 1, 5), "-12.99")]
    )
    out = svc.detect(straddling, [], today=TODAY)
    assert [s.occurrences for s in out] == [3]
    assert out[0].first_date == date(2023, 11, 5)
