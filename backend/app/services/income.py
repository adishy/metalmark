"""Owner income profile and paystubs (ADR-0052).

Two write paths, one validation rule: a paystub's header (``gross``/``net``)
must agree with its lines when it has any. ``_check_totals`` is the one place
that rule is enforced, so the create and patch paths cannot drift apart.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.income import OwnerIncomeProfile, Paystub, PaystubLine
from app.schemas.income import (
    IncomeSummaryOut,
    OwnerIncomeProfileUpdate,
    PaystubCreate,
    PaystubLineIn,
    PaystubPatch,
    YtdKindTotal,
)
from app.schemas.patch import is_set
from app.services.errors import LedgerError
from app.services.ledger import base_currency
from app.services.ownership import require_owners

#: Annual-equivalent multiplier per pay frequency — a monthly-equivalent
#: figure the summary reports, the same convention ``services/recurring`` will
#: use for cadence (task A3), stated once here.
ANNUAL_MULTIPLIER = {
    "weekly": Decimal(52),
    "biweekly": Decimal(26),
    "semimonthly": Decimal(24),
    "monthly": Decimal(12),
    "annual": Decimal(1),
}

#: Rounding tolerance for the gross/net-vs-lines check. Paystubs are entered by
#: hand from a printed or PDF document; a stub itself sometimes rounds a
#: sub-line to the cent in a way that is a penny off its own total. Real
#: mismatches are dollars, not cents, so a cent of slack catches transcription
#: rounding without hiding an actual error.
_TOTALS_TOLERANCE = Decimal("0.01")


def _check_totals(gross: Decimal, net: Decimal, lines: list[PaystubLineIn]) -> None:
    if not lines:
        return
    by_kind: dict[str, Decimal] = {}
    for line in lines:
        by_kind[line.kind] = by_kind.get(line.kind, Decimal(0)) + line.amount

    earnings = by_kind.get("earning", Decimal(0))
    pre_tax = by_kind.get("pre_tax_deduction", Decimal(0))
    tax = by_kind.get("tax", Decimal(0))
    post_tax = by_kind.get("post_tax_deduction", Decimal(0))
    # employer_contribution is deliberately excluded from both sides — it is
    # never the owner's gross or net (ADR-0052).

    if abs(earnings - gross) > _TOTALS_TOLERANCE:
        raise LedgerError(
            f"Gross ({gross}) does not match the sum of earning lines ({earnings})", 422,
        )
    expected_net = gross - pre_tax - tax - post_tax
    if abs(expected_net - net) > _TOTALS_TOLERANCE:
        raise LedgerError(
            f"Net ({net}) does not match gross minus deductions and taxes "
            f"({expected_net})",
            422,
        )


def _apply_lines(paystub: Paystub, lines: list[PaystubLineIn]) -> None:
    paystub.lines.clear()
    for i, line in enumerate(lines):
        paystub.lines.append(
            PaystubLine(
                household_id=paystub.household_id,
                kind=line.kind,
                label=line.label,
                amount=line.amount,
                ytd_amount=line.ytd_amount,
                position=line.position if line.position is not None else i,
            )
        )


# ---- income profile -----------------------------------------------------------


async def get_profile(session: AsyncSession, owner_id: uuid.UUID) -> OwnerIncomeProfile | None:
    return (
        await session.execute(
            select(OwnerIncomeProfile).where(OwnerIncomeProfile.owner_id == owner_id)
        )
    ).scalar_one_or_none()


async def upsert_profile(
    session: AsyncSession,
    household_id: uuid.UUID,
    owner_id: uuid.UUID,
    data: OwnerIncomeProfileUpdate,
) -> OwnerIncomeProfile:
    """Create the profile on first write, update it after. One route (``PUT``)
    covers both, since a household either has an owner's profile or has never
    stated anything about it — there is no meaningful "create" step to gate
    separately."""
    await require_owners(session, [owner_id])
    profile = await get_profile(session, owner_id)
    if profile is None:
        profile = OwnerIncomeProfile(
            household_id=household_id,
            owner_id=owner_id,
            currency=data.currency or await base_currency(session, household_id),
        )
        session.add(profile)
    elif data.currency is not None:
        profile.currency = data.currency

    if is_set(data, "annual_gross_income"):
        profile.annual_gross_income = data.annual_gross_income
    if is_set(data, "pay_frequency"):
        profile.pay_frequency = data.pay_frequency
    if is_set(data, "filing_status"):
        profile.filing_status = data.filing_status
    if is_set(data, "tax_region"):
        profile.tax_region = data.tax_region

    await session.flush()
    return profile


async def compute_summary(
    session: AsyncSession, owner_id: uuid.UUID, profile: OwnerIncomeProfile | None,
    *, today: date | None = None,
) -> IncomeSummaryOut:
    """Annualized gross, this year's totals by line kind, and the effective
    tax rate — all derived at read time from the profile and the owner's
    paystubs (ADR-0052 / ADR-0035's "read once" discipline)."""
    year = (today or date.today()).year
    rows = (
        await session.execute(
            select(PaystubLine.kind, PaystubLine.amount, Paystub.gross)
            .join(Paystub, PaystubLine.paystub_id == Paystub.id)
            .where(Paystub.owner_id == owner_id, extract("year", Paystub.pay_date) == year)
        )
    ).all()

    ytd: dict[str, Decimal] = {}
    ytd_gross = Decimal(0)
    seen_paystubs: set = set()
    for kind, amount, _gross in rows:
        ytd[kind] = ytd.get(kind, Decimal(0)) + amount
    # Sum gross across this year's paystubs once per paystub, not once per line.
    gross_rows = (
        await session.execute(
            select(Paystub.id, Paystub.gross)
            .where(Paystub.owner_id == owner_id, extract("year", Paystub.pay_date) == year)
        )
    ).all()
    for pid, gross in gross_rows:
        if pid not in seen_paystubs:
            seen_paystubs.add(pid)
            ytd_gross += gross

    effective_tax_rate = None
    if ytd_gross > 0:
        effective_tax_rate = (ytd.get("tax", Decimal(0)) / ytd_gross).quantize(Decimal("0.0001"))

    annualized_gross = None
    if profile is not None and profile.annual_gross_income is not None:
        annualized_gross = profile.annual_gross_income
    else:
        latest = (
            await session.execute(
                select(Paystub.gross, Paystub.pay_date)
                .where(Paystub.owner_id == owner_id)
                .order_by(Paystub.pay_date.desc())
                .limit(1)
            )
        ).first()
        if latest is not None:
            gross, _pay_date = latest
            freq = profile.pay_frequency if profile is not None else None
            multiplier = ANNUAL_MULTIPLIER.get(freq) if freq else None
            if multiplier is not None:
                annualized_gross = (gross * multiplier).quantize(Decimal("0.01"))

    return IncomeSummaryOut(
        annualized_gross=annualized_gross,
        ytd=[YtdKindTotal(kind=k, amount=v) for k, v in sorted(ytd.items())],
        effective_tax_rate=effective_tax_rate,
    )


# ---- paystubs -------------------------------------------------------------


async def list_paystubs(session: AsyncSession, owner_id: uuid.UUID) -> list[Paystub]:
    return list(
        (
            await session.execute(
                select(Paystub)
                .where(Paystub.owner_id == owner_id)
                .order_by(Paystub.pay_date.desc())
                .options(selectinload(Paystub.lines))
            )
        )
        .unique()
        .scalars()
        .all()
    )


async def get_paystub(session: AsyncSession, paystub_id: uuid.UUID) -> Paystub:
    paystub = (
        await session.execute(
            select(Paystub)
            .where(Paystub.id == paystub_id)
            .options(selectinload(Paystub.lines))
        )
    ).scalar_one_or_none()
    if paystub is None:
        raise LedgerError("Paystub not found", 404)
    return paystub


async def create_paystub(
    session: AsyncSession, household_id: uuid.UUID, owner_id: uuid.UUID, data: PaystubCreate,
) -> Paystub:
    await require_owners(session, [owner_id])
    _check_totals(data.gross, data.net, data.lines)
    paystub = Paystub(
        household_id=household_id,
        owner_id=owner_id,
        pay_date=data.pay_date,
        period_start=data.period_start,
        period_end=data.period_end,
        employer=data.employer,
        currency=data.currency or await base_currency(session, household_id),
        gross=data.gross,
        net=data.net,
    )
    _apply_lines(paystub, data.lines)
    session.add(paystub)
    await session.flush()
    # Re-fetch with `lines` eager-loaded (same reasoning as `replace_splits`
    # below): an async session cannot lazy-load a relationship on demand, and
    # a caller serializing the result needs `.lines` populated either way.
    return await get_paystub(session, paystub.id)


async def update_paystub(
    session: AsyncSession, paystub_id: uuid.UUID, data: PaystubPatch,
) -> Paystub:
    paystub = await get_paystub(session, paystub_id)

    gross = data.gross if is_set(data, "gross") and data.gross is not None else paystub.gross
    net = data.net if is_set(data, "net") and data.net is not None else paystub.net
    lines = data.lines if is_set(data, "lines") else [
        PaystubLineIn(
            kind=line.kind, label=line.label, amount=line.amount,
            ytd_amount=line.ytd_amount, position=line.position,
        )
        for line in paystub.lines
    ]
    _check_totals(gross, net, lines or [])

    if is_set(data, "pay_date") and data.pay_date is not None:
        paystub.pay_date = data.pay_date
    if is_set(data, "period_start"):
        paystub.period_start = data.period_start
    if is_set(data, "period_end"):
        paystub.period_end = data.period_end
    if is_set(data, "employer"):
        paystub.employer = data.employer
    if is_set(data, "currency") and data.currency is not None:
        paystub.currency = data.currency
    paystub.gross = gross
    paystub.net = net
    if is_set(data, "lines") and data.lines is not None:
        _apply_lines(paystub, data.lines)

    await session.flush()
    return paystub


async def delete_paystub(session: AsyncSession, paystub_id: uuid.UUID) -> None:
    paystub = await get_paystub(session, paystub_id)
    await session.delete(paystub)
    await session.flush()
