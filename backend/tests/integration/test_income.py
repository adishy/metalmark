"""Owner income profile and paystubs (ADR-0052): the gross/net-vs-lines check,
the derived summary, RLS isolation, and cascade on owner delete.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import scoped_session
from app.models import OwnerIncomeProfile, Paystub, PaystubLine
from app.schemas.income import (
    OwnerIncomeProfileUpdate,
    PaystubCreate,
    PaystubLineIn,
    PaystubPatch,
)
from app.services import income, owners
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal


async def _owner(session, household_id, name="Alex"):
    return await owners.create_owner(session, household_id, name=name)


# ---- income profile ---------------------------------------------------------


async def test_a_profile_defaults_its_currency_to_the_household_base(household_factory):
    hh = await household_factory(base="EUR")
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        profile = await income.upsert_profile(
            s, hh, alex.id, OwnerIncomeProfileUpdate(annual_gross_income=D("90000"))
        )
        assert profile.currency == "EUR"
        assert profile.annual_gross_income == D("90000")


async def test_put_is_upsert_and_absent_fields_are_left_alone(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        await income.upsert_profile(
            s, hh, alex.id,
            OwnerIncomeProfileUpdate(annual_gross_income=D("100000"), pay_frequency="biweekly"),
        )
        # A second PUT that only sends filing_status must not clear the fields
        # the first one set.
        updated = await income.upsert_profile(
            s, hh, alex.id, OwnerIncomeProfileUpdate(filing_status="single"),
        )
        assert updated.annual_gross_income == D("100000")
        assert updated.pay_frequency == "biweekly"
        assert updated.filing_status == "single"


async def test_an_invalid_pay_frequency_is_rejected_at_the_schema(household_factory):
    with pytest.raises(ValidationError):
        OwnerIncomeProfileUpdate(pay_frequency="fortnightly")


async def test_negative_gross_income_is_rejected_at_the_schema():
    with pytest.raises(ValidationError):
        OwnerIncomeProfileUpdate(annual_gross_income=D("-1"))


async def test_a_profile_for_an_unknown_owner_is_a_404(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        with pytest.raises(LedgerError) as exc:
            await income.upsert_profile(s, hh, uuid.uuid4(), OwnerIncomeProfileUpdate())
    assert exc.value.status == 404


# ---- paystub gross/net vs lines --------------------------------------------


def _line(kind, label, amount, ytd=None):
    return PaystubLineIn(kind=kind, label=label, amount=D(amount), ytd_amount=ytd)


async def test_a_paystub_with_matching_lines_is_accepted(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        paystub = await income.create_paystub(
            s, hh, alex.id,
            PaystubCreate(
                pay_date="2026-09-15", employer="Acme", gross=D("4615.38"), net=D("3200.00"),
                lines=[
                    _line("earning", "Salary", "4615.38"),
                    _line("pre_tax_deduction", "401k", "200.00"),
                    _line("tax", "Federal", "700.00"),
                    _line("tax", "Social Security", "286.15"),
                    _line("tax", "Medicare", "66.92"),
                    _line("post_tax_deduction", "Health insurance", "162.31"),
                    _line("employer_contribution", "401k match", "100.00"),
                ],
            ),
        )
        assert paystub.gross == D("4615.38")
        assert len(paystub.lines) == 7
        # employer_contribution is excluded from net: 4615.38 - 200 - (700+286.15
        # +66.92) - 162.31 = 3199.999999999999... — matches net within tolerance.


async def test_a_paystub_with_no_lines_is_allowed(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        paystub = await income.create_paystub(
            s, hh, alex.id,
            PaystubCreate(pay_date="2026-09-15", gross=D("1000.00"), net=D("800.00")),
        )
        assert paystub.lines == []


async def test_a_gross_mismatched_with_earning_lines_is_a_422(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        with pytest.raises(LedgerError) as exc:
            await income.create_paystub(
                s, hh, alex.id,
                PaystubCreate(
                    pay_date="2026-09-15", gross=D("1000.00"), net=D("800.00"),
                    lines=[_line("earning", "Salary", "900.00")],
                ),
            )
        assert exc.value.status == 422
        assert "Gross" in exc.value.message


async def test_a_net_mismatched_with_deductions_is_a_422(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        with pytest.raises(LedgerError) as exc:
            await income.create_paystub(
                s, hh, alex.id,
                PaystubCreate(
                    pay_date="2026-09-15", gross=D("1000.00"), net=D("500.00"),
                    lines=[
                        _line("earning", "Salary", "1000.00"),
                        _line("tax", "Federal", "100.00"),
                    ],
                ),
            )
        assert exc.value.status == 422
        assert "Net" in exc.value.message


async def test_updating_lines_is_replace_all_and_omitting_lines_leaves_them(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        paystub = await income.create_paystub(
            s, hh, alex.id,
            PaystubCreate(
                pay_date="2026-09-15", gross=D("1000.00"), net=D("1000.00"),
                lines=[_line("earning", "Salary", "1000.00")],
            ),
        )

        # A PATCH that only touches `employer` must not disturb the lines.
        await income.update_paystub(s, paystub.id, PaystubPatch(employer="Acme"))
        reloaded = await income.get_paystub(s, paystub.id)
        assert [line.label for line in reloaded.lines] == ["Salary"]

        # A PATCH that sends `lines` replaces the whole set.
        await income.update_paystub(
            s, paystub.id,
            PaystubPatch(lines=[_line("earning", "Salary", "1000.00"),
                                _line("tax", "Federal", "0.00")]),
        )
        reloaded = await income.get_paystub(s, paystub.id)
        assert sorted(line.label for line in reloaded.lines) == ["Federal", "Salary"]

        # And `"lines": []` really does clear them.
        await income.update_paystub(s, paystub.id, PaystubPatch(lines=[]))
        reloaded = await income.get_paystub(s, paystub.id)
        assert reloaded.lines == []


# ---- summary ----------------------------------------------------------------


async def test_summary_annualizes_from_the_profile_when_stated(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        profile = await income.upsert_profile(
            s, hh, alex.id, OwnerIncomeProfileUpdate(annual_gross_income=D("120000")),
        )
        summary = await income.compute_summary(s, alex.id, profile)
        assert summary.annualized_gross == D("120000")


async def test_summary_annualizes_from_the_latest_paystub_when_no_profile_figure(
    household_factory,
):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        profile = await income.upsert_profile(
            s, hh, alex.id, OwnerIncomeProfileUpdate(pay_frequency="biweekly"),
        )
        await income.create_paystub(
            s, hh, alex.id,
            PaystubCreate(pay_date="2026-09-15", gross=D("4615.38"), net=D("4615.38")),
        )
        summary = await income.compute_summary(s, alex.id, profile)
        assert summary.annualized_gross == D("4615.38") * 26


async def test_summary_ytd_and_effective_tax_rate(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        for pay_date in ("2026-01-15", "2026-02-15"):
            await income.create_paystub(
                s, hh, alex.id,
                PaystubCreate(
                    pay_date=pay_date, gross=D("1000.00"), net=D("800.00"),
                    lines=[
                        _line("earning", "Salary", "1000.00"),
                        _line("tax", "Federal", "200.00"),
                    ],
                ),
            )
        summary = await income.compute_summary(s, alex.id, None, today=date(2026, 6, 1))
        ytd = {row.kind: row.amount for row in summary.ytd}
        assert ytd["earning"] == D("2000.00")
        assert ytd["tax"] == D("400.00")
        # 400 tax / 2000 gross
        assert summary.effective_tax_rate == D("0.2000")


# ---- RLS and cascade --------------------------------------------------------


async def test_a_paystub_in_another_household_is_a_404(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        alex = await _owner(s, a)
        paystub = await income.create_paystub(
            s, a, alex.id, PaystubCreate(pay_date="2026-09-15", gross=D("1"), net=D("1")),
        )
    async with scoped_session(household_id=b) as s:
        with pytest.raises(LedgerError) as exc:
            await income.get_paystub(s, paystub.id)
    assert exc.value.status == 404


async def test_deleting_an_owner_cascades_its_profile_and_paystubs(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await _owner(s, hh)
        await income.upsert_profile(s, hh, alex.id, OwnerIncomeProfileUpdate())
        paystub = await income.create_paystub(
            s, hh, alex.id, PaystubCreate(pay_date="2026-09-15", gross=D("1"), net=D("1")),
        )
        await owners.delete_owner(s, alex, reassign_to=None)

        assert (
            await s.execute(
                select(OwnerIncomeProfile).where(OwnerIncomeProfile.owner_id == alex.id)
            )
        ).scalar_one_or_none() is None
        assert (
            await s.execute(select(Paystub).where(Paystub.id == paystub.id))
        ).scalar_one_or_none() is None
        assert (
            await s.execute(select(PaystubLine).where(PaystubLine.paystub_id == paystub.id))
        ).scalar_one_or_none() is None
