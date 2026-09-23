"""Data checks (ADR-0047): what they find, and what they never write to the log."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import structlog
from sqlalchemy import delete, select

from app.db import scoped_session, unscoped_session
from app.models import Account, FxRate, Owner
from app.schemas.ledger import AccountCreate
from app.services import checks, ledger

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture(autouse=True)
async def _clean_rates():
    """Leave no BGN/THB rate behind: ``fx_rates`` is shared by every test."""
    yield
    async with unscoped_session() as s:
        for col in (FxRate.base_currency, FxRate.quote_currency):
            await s.execute(delete(FxRate).where(col.in_(["BGN", "THB"])))


def _by_id(results) -> dict[str, checks.Check]:
    return {c.id: c for c in results}


async def test_a_clean_household_passes(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        await ledger.create_account(s, hid, AccountCreate(
            name="Checking", type="depository", currency="USD",
            current_balance=D("1000"), balance_date=date(2026, 1, 1)))
        await ledger.create_account(s, hid, AccountCreate(
            name="Card", type="credit", currency="USD",
            current_balance=D("-200"), balance_date=date(2026, 1, 1)))
        results = await checks.run(s, hid)
    assert {c.id: c.status for c in results} == {
        "headline_matches_chart": "ok",
        "synced_investments_valued": "ok",
        "liabilities_signed": "ok",
        "manual_liabilities_positive": "ok",
        "accounts_without_rate": "ok",
        "stale_accounts": "ok",
        "migrations_applied": "info",
    }


async def test_the_headline_agrees_across_currencies_and_hidden_accounts(household_factory):
    """Strict equality is the point of this check, so it has to hold where rounding
    could bite: rates stored in both directions (one divides), odd amounts, a
    liability, and a hidden account the headline leaves out."""
    hid = await household_factory()
    today = ledger.today()
    async with unscoped_session() as s:
        s.add(FxRate(base_currency="BGN", quote_currency="USD", rate_date=today,
                     rate=D("0.55683117"), source="manual"))
        s.add(FxRate(base_currency="USD", quote_currency="THB", rate_date=today,
                     rate=D("33.17"), source="manual"))
    async with scoped_session(hid) as s:
        for name, type_, ccy, bal in (
            ("Leva", "depository", "BGN", "1234.57"),
            ("Baht", "depository", "THB", "98765.43"),
            ("Baht card", "credit", "THB", "-4321.09"),
            ("Dollars", "depository", "USD", "10.01"),
        ):
            await ledger.create_account(s, hid, AccountCreate(
                name=name, type=type_, currency=ccy, current_balance=D(bal),
                balance_date=date(2026, 1, 1)))
        hidden = await ledger.create_account(s, hid, AccountCreate(
            name="Hidden", type="depository", currency="THB",
            current_balance=D("777.77"), balance_date=date(2026, 1, 1)))
        hidden.is_hidden = True
        await s.flush()
        found = _by_id(await checks.run(s, hid))
    assert found["headline_matches_chart"].status == "ok"
    assert found["accounts_without_rate"].status == "ok"


async def test_a_crashing_check_is_a_finding_and_leaks_nothing(household_factory, monkeypatch):
    """A database error's message carries the statement's parameters. The check
    that raised it becomes a failed finding naming only the exception's type; the
    other checks still run; and the log never sees the message."""
    hid = await _problems(household_factory)

    async def boom(session):
        raise RuntimeError("UPDATE accounts SET name='Amex Secret' balance=450.37")

    monkeypatch.setattr(checks, "_stale_accounts", boom)
    async with scoped_session(hid) as s:
        found = _by_id(await checks.run(s, hid))
    assert found["stale_accounts"].status == "fail"
    assert found["stale_accounts"].summary == "This check could not run (RuntimeError)."
    assert found["migrations_applied"].status == "info"

    from app import worker

    async def crash_everything(session, household_id):
        raise RuntimeError("SELECT … [parameters: ('Brokerage Secret', 91000.53)]")

    monkeypatch.setattr(checks, "run", crash_everything)
    with structlog.testing.capture_logs() as logs:
        await worker.startup_checks()
    crashed = [e for e in logs if e.get("event") == "checks.crashed"
               and e.get("household_id") == str(hid)]
    assert crashed and crashed[0]["error_type"] == "RuntimeError"
    assert crashed[0]["at"].endswith(".py:" + crashed[0]["at"].rsplit(":", 1)[1])
    for secret in ("Secret", "450.37", "91000.53"):
        assert secret not in repr(logs), f"{secret!r} reached the log"


async def _problems(household_factory):
    hid = await household_factory()
    async with scoped_session(hid) as s:
        owner = (await s.execute(select(Owner.id).limit(1))).scalar_one()

        def synced(name, type_, balance, source=None, currency="USD"):
            a = Account(household_id=hid, name=name, type=type_, currency=currency,
                        current_balance=D(balance), balance_date=date(2026, 1, 1),
                        is_asset=type_ not in ("credit", "loan"), owner_id=owner,
                        is_manual=False, balance_source=source,
                        external_key=f"bank:{name.lower()}", external_id=f"ACT-{name}")
            s.add(a)
            return a

        synced("Amex Secret", "credit", "450.37")
        synced("Brokerage Secret", "investment", "91000.53", source="derived")
        await ledger.create_account(s, hid, AccountCreate(
            name="Hand Card Secret", type="credit", currency="USD",
            current_balance=D("120.41"), balance_date=date(2026, 1, 1)))
        await ledger.create_account(s, hid, AccountCreate(
            name="Forint Secret", type="depository", currency="HUF",
            current_balance=D("100000.29"), balance_date=date(2026, 1, 1)))
        await s.flush()
    return hid


async def test_each_finding_names_its_accounts(household_factory):
    hid = await _problems(household_factory)
    async with scoped_session(hid) as s:
        found = _by_id(await checks.run(s, hid))
    assert found["synced_investments_valued"].status == "fail"
    assert [i.name for i in found["synced_investments_valued"].items] == ["Brokerage Secret"]
    assert [i.name for i in found["liabilities_signed"].items] == ["Amex Secret"]
    assert [i.name for i in found["manual_liabilities_positive"].items] == [
        "Hand Card Secret"]
    assert [i.name for i in found["accounts_without_rate"].items] == ["Forint Secret"]


async def test_the_worker_logs_shape_never_names_or_amounts(household_factory):
    """Stdout goes wherever the host sends container logs. A household's account
    names and balances do not belong there — the checks' ids, statuses and counts
    are all the worker says."""
    hid = await _problems(household_factory)
    from app import worker

    with structlog.testing.capture_logs() as logs:
        await worker.startup_checks()
    ours = [e for e in logs if e.get("event") == "checks.completed"
            and e.get("household_id") == str(hid)]
    assert len(ours) == 1
    assert ours[0]["checks"]["synced_investments_valued"] == {"status": "fail", "count": 1}
    assert ours[0]["log_level"] == "warning"
    text = repr(logs)
    # Distinctive on purpose: a name word no uuid can contain, and amounts with a
    # decimal point, which no id in the log has either.
    for secret in ("Secret", "450.37", "91000.53", "120.41", "100000.29"):
        assert secret not in text, f"{secret!r} reached the log"


async def test_the_api_serves_the_checks_to_the_household():
    from app.main import create_app
    from tests.integration.test_api_owners import Client, _signup

    async with Client(create_app()) as client:
        await _signup(client)
        resp = await client.get("/checks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == "0007"
    assert [c["id"] for c in body["checks"]][0] == "headline_matches_chart"
