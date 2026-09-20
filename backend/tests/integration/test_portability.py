"""Portable export/import — ADR-0036.

Two properties carry the whole design, and both are about what *does not* happen:

**Nothing secret is in the document.** ``access_url_encrypted`` is a live bank
bearer token, so the test that matters is not "the exporter redacts it" but "no
export byte contains it, under any spelling, including the plaintext the
ciphertext decrypts to". That test seeds a real ``SecretBox`` ciphertext so it
would notice a future edit that added the column to a field list.

**A second import creates nothing.** The document is written to be re-importable,
which means the second import is the interesting one: it is where a natural key
that was chosen badly shows up as a duplicate rather than as a passing test.

The tests run against a *fresh* household rather than the same one, because that
is the case an export exists for — and because importing into the household you
exported from is the easy case that would hide every remapping bug.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db import scoped_session
from app.models import (
    Account,
    AccountConnection,
    Category,
    CategoryGroup,
    FxRate,
    Holding,
    InvestmentTransaction,
    Owner,
    Rule,
    Security,
    SecurityPrice,
    Tag,
    Transaction,
    TransactionSplit,
    TransactionTag,
)
from app.schemas.transactions import TransactionCreate
from app.security.crypto import SecretBox
from app.services import imports as imports_svc
from app.services import ledger, portability
from app.services import owners as owners_svc
from app.services import reports as reports_svc
from app.services import transactions as txns

pytestmark = pytest.mark.integration

D = Decimal
ON = date(2026, 3, 4)
NOON = datetime(2026, 3, 4, 12, tzinfo=UTC)

#: The credential a connection would carry. Written into a real ``SecretBox``
#: ciphertext below and then searched for in every export byte — the point is that
#: the *decrypted* value is what must not appear, so a test that only looked for
#: the ciphertext would pass on an exporter that decrypted and re-serialized.
ACCESS_URL = "https://user:hunter2@bridge.example.test/simplefin/accounts"


async def _seed(session, hh):
    """A household with one of everything the document carries.

    Not a round number of each: the counts asserted below are what says which
    entities were created and which were matched, so a seed that happened to hold
    zero of something would make its assertion vacuous.
    """
    owner = Owner(household_id=hh, name="Alex", kind="person", sort=1)
    session.add(owner)
    group = CategoryGroup(household_id=hh, name="Living", type="expense", sort=0)
    session.add(group)
    await session.flush()
    category = Category(household_id=hh, group_id=group.id, name="Groceries",
                        icon="cart", color="#ff8800", sort=3)
    tag = Tag(household_id=hh, name="reimbursable", color="#0088ff")
    session.add_all([category, tag])
    await session.flush()

    security = Security(household_id=hh, name="Total Market", ticker="VTI",
                        security_type="etf", currency="USD")
    session.add(security)
    await session.flush()
    session.add(SecurityPrice(household_id=hh, security_id=security.id,
                              price_date=ON, price=D("250.12345678"), currency="USD"))

    checking = Account(household_id=hh, name="Checking", type="depository",
                       currency="USD", current_balance=D("1200.5000"),
                       balance_date=ON, is_asset=True, owner_id=owner.id,
                       is_manual=True)
    broker = Account(household_id=hh, name="Brokerage", type="investment",
                     currency="USD", current_balance=D("500.0000"), balance_source="derived",
                     is_asset=True, owner_id=owner.id, is_manual=True)
    session.add_all([checking, broker])
    await session.flush()
    session.add(Holding(household_id=hh, account_id=broker.id, security_id=security.id,
                        quantity=D("2.00000000"), cost_basis=D("400.0000"), as_of=ON))
    await ledger.upsert_balance_snapshot(session, checking)
    await ledger.upsert_balance_snapshot(session, broker)

    # Every id-bearing key the rule blob schema has, so the remap is exercised on
    # all of them at once: two scalars, two lists, and both keys inside a split
    # leg. The legs are a valid pair (ADR-0031: one amount leg, one remainder).
    rule = Rule(household_id=hh, priority=10, name="Tag the market",
                conditions={"merchant_contains": "MARKET", "category_id": str(category.id)},
                actions={"set_category_id": str(category.id), "add_tag_ids": [str(tag.id)],
                         "set_owner_id": str(owner.id),
                         "split": [{"amount": "1.0000", "category_id": str(category.id),
                                    "owner_id": str(owner.id)},
                                   {"remainder": True}]})
    session.add(rule)

    # A provider row (deduped on external_id) and a manual one (deduped on the
    # recomputed import_hash) — the two halves of the ledger's dedupe rule, and
    # the reason the import has to handle both.
    first = await txns.create_transaction(
        session, hh,
        TransactionCreate(account_id=checking.id, amount=D("-42.5000"),
                          transacted_at=NOON, description="Provider row"),
        source="simplefin",
    )
    first.external_id = "sf-1234"
    manual = await txns.create_transaction(
        session, hh,
        TransactionCreate(account_id=checking.id, amount=D("-9.9900"),
                          transacted_at=datetime(2026, 3, 5, 12, tzinfo=UTC),
                          description="Manual row", category_id=category.id,
                          owner_id=owner.id, notes="a note"),
        source="manual",
    )
    second = await txns.create_transaction(
        session, hh,
        TransactionCreate(account_id=checking.id, amount=D("-9.9900"),
                          transacted_at=datetime(2026, 3, 5, 12, tzinfo=UTC),
                          description="Manual row"),
        source="manual",
    )
    await session.flush()
    session.add_all([
        TransactionSplit(parent_txn_id=manual.id, amount=D("-5.0000"),
                         category_id=category.id, owner_id=owner.id, notes="half"),
        TransactionSplit(parent_txn_id=manual.id, amount=D("-4.9900")),
        TransactionTag(transaction_id=manual.id, tag_id=tag.id),
    ])
    await session.flush()

    await ledger.upsert_fx_rate(session, hh, base_ccy="USD", quote_ccy="AUD",
                                rate_date=ON, rate=D("1.52500000"))
    return {
        "owner": owner, "group": group, "category": category, "tag": tag,
        "security": security, "checking": checking, "broker": broker, "rule": rule,
        "manual": manual, "second": second, "first": first,
    }


async def _export(session, hh):
    return await portability.export_document(session, hh)


async def _roundtrip(source_hh, target_hh):
    async with scoped_session(household_id=source_hh) as s:
        document = await _export(s, source_hh)
    raw = portability.dumps(document)
    async with scoped_session(household_id=target_hh) as s:
        result = await portability.import_document(s, target_hh, raw)
    return document, result


# ---- the round trip ---------------------------------------------------------


async def test_a_household_survives_a_round_trip_into_a_fresh_one(household_factory):
    source = await household_factory(name="Source")
    target = await household_factory(name="Target", base="USD")
    async with scoped_session(household_id=source) as s:
        seeded = await _seed(s, source)

    document, result = await _roundtrip(source, target)

    async with scoped_session(household_id=target) as s:
        by_name = {
            acc.name: acc for acc in (await s.execute(select(Account))).scalars().all()
        }
        checking = by_name["Checking"]
        broker = by_name["Brokerage"]
        # Exactly the source's two: a household is seeded with a Shared *owner*,
        # never a Shared account, so an import must add two accounts and no more.
        assert set(by_name) == {"Checking", "Brokerage"}

        # Money is exact, to the scale the database stores.
        assert checking.current_balance == D("1200.5000")
        assert checking.balance_date == ON
        assert broker.balance_source == "derived"

        # The category and its group arrived, with the category filed under it.
        category = (
            await s.execute(select(Category).where(Category.name == "Groceries"))
        ).scalar_one()
        group = (
            await s.execute(select(CategoryGroup).where(CategoryGroup.name == "Living"))
        ).scalar_one()
        assert category.group_id == group.id
        assert category.icon == "cart" and category.color == "#ff8800"

        owner = (await s.execute(select(Owner).where(Owner.name == "Alex"))).scalar_one()
        tag = (await s.execute(select(Tag).where(Tag.name == "reimbursable"))).scalar_one()
        # The account's owner is the *remapped* Alex, not the source household's.
        assert checking.owner_id == owner.id
        assert owner.id != seeded["owner"].id

        # Both transactions, and the second of the identical pair is a separate
        # row — the ordinal in the digest is what keeps them that way.
        rows = (
            await s.execute(select(Transaction).order_by(Transaction.transacted_at))
        ).scalars().all()
        assert len(rows) == 3
        provider = next(r for r in rows if r.external_id == "sf-1234")
        assert provider.import_hash is None  # the provider key is the identity
        assert provider.amount == D("-42.5000")
        manuals = [r for r in rows if r.description == "Manual row"]
        assert len(manuals) == 2
        assert sorted(r.import_hash for r in manuals) == sorted(
            r.import_hash for r in manuals
        ) and len({r.import_hash for r in manuals}) == 2

        # The manual row's category, owner, notes and provenance carried over.
        filed = next(r for r in manuals if r.category_id is not None)
        assert filed.category_id == category.id
        assert filed.owner_id == owner.id
        assert filed.notes == "a note"

        # Its splits and its tag, on the *new* parent.
        splits = (await s.execute(select(TransactionSplit))).scalars().all()
        assert len(splits) == 2
        assert {sp.category_id for sp in splits} == {category.id, None}
        assert (await s.execute(select(TransactionTag))).scalars().all()[0].tag_id == tag.id
        assert (await s.execute(select(Transaction).where(
            Transaction.id == filed.id))).scalar_one().is_split_parent is True

        # The security, its price at full NUMERIC(19,8) precision, and the holding.
        security = (
            await s.execute(select(Security).where(Security.ticker == "VTI"))
        ).scalar_one()
        price = (await s.execute(select(SecurityPrice))).scalar_one()
        assert price.security_id == security.id
        assert price.price == D("250.12345678")
        holding = (await s.execute(select(Holding))).scalar_one()
        assert holding.account_id == broker.id and holding.security_id == security.id
        assert holding.quantity == D("2.00000000")

        # The rule's JSONB references were rewritten to the new ids — the check
        # that would catch a remap that forgot it has no foreign key to lean on.
        rule = (await s.execute(select(Rule).where(Rule.name == "Tag the market"))).scalar_one()
        assert rule.conditions["category_id"] == str(category.id)
        assert rule.actions["set_category_id"] == str(category.id)
        assert rule.actions["add_tag_ids"] == [str(tag.id)]
        assert rule.actions["set_owner_id"] == str(owner.id)
        assert rule.actions["split"][0]["category_id"] == str(category.id)
        assert rule.actions["split"][0]["owner_id"] == str(owner.id)
        # A leg with no ids on it is a leg the remap must leave alone.
        assert rule.actions["split"][1] == {"remainder": True}
        # …and a string that is not an id was left alone.
        assert rule.conditions["merchant_contains"] == "MARKET"

        # Filtered on the table's whole unique key, not just the currency:
        # ``fx_rates`` is global and has no household, so a looser filter also
        # matches rates every other test in the run has written for AUD.
        rate = (
            await s.execute(
                select(FxRate).where(
                    FxRate.base_currency == "USD",
                    FxRate.quote_currency == "AUD",
                    FxRate.rate_date == ON,
                )
            )
        ).scalar_one()
        assert rate.rate == D("1.52500000")

    # And the counts say the same thing: everything was created.
    assert result.created["accounts"] == 2
    assert result.created["transactions"] == 3
    assert result.created["categories"] == 1
    assert result.created["rules"] == 1
    # The rate is here and usable, which is the property. Whether *this* import is
    # the one that wrote it is not: ``fx_rates`` is global, so a rate another
    # household already recorded is one this import correctly leaves alone — and
    # which of the two happened depends on what else this database has seen.
    #
    # Which is why the count is "at least one" rather than "exactly one". The
    # export takes every rate the table holds, because there is no household
    # column to scope it by, so the number here is a fact about this database
    # rather than about the document — and the assertion that the rate *arrived*
    # is the query above, which pins the rate and its value.
    assert result.created.get("fx_rates", 0) + result.matched.get("fx_rates", 0) >= 1
    # The only other thing that matched is the target's own Shared owner, which
    # every household has before an import touches it — so it is always a match,
    # and a document that *created* one would be the bug.
    assert result.matched.get("owners") == 1
    assert set(result.matched) <= {"owners", "fx_rates"}


async def test_the_two_households_report_the_same_numbers(household_factory):
    """The round trip at the level a person checks it, not the level it is stored.

    Row equality is necessary and not sufficient, and the gap between the two is
    the whole reason this test exists: every report joins rows, so a remap that
    filed a transaction under the right account and the *wrong* category is a
    round trip that passes a row count while changing the answer to "what did we
    spend". Nothing in the row-level test above can see that, because the rows are
    all present and all correct — it is the references between them that moved.

    So the assertion is that the two households *report* the same thing over the
    same window, through the same three service calls the API makes. It is the
    v0.9 definition of done read literally: "the two reconcile to the same net
    worth, the same transaction count, and the same per-category totals".
    """
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    source = await household_factory(name="Source")
    target = await household_factory(name="Target", base="USD")
    async with scoped_session(household_id=source) as s:
        await _seed(s, source)
    await _roundtrip(source, target)

    async def reported(hh):
        async with scoped_session(household_id=hh) as s:
            worth = await reports_svc.net_worth_series(s, hh, start, end)
            base, _granularity, flows = await reports_svc.cash_flow_series(s, hh, start, end)
            spend_base, rows, total, spend_warnings = await reports_svc.spending_by_category(
                s, hh, start, end
            )
            count = (
                await s.execute(select(func.count()).select_from(Transaction))
            ).scalar_one()
        return {
            # Points only: the decomposition beside them is keyed by account id,
            # and an id is the one thing a round trip must *not* reproduce.
            "net worth": [(p["date"], p["net_worth"]) for p in worth["points"]],
            "net worth change": worth["delta_net_worth"],
            "net cash flow": worth["net_cash_flow"],
            # Zero in both, and asserted in both rather than skipped: a remap that
            # lost the link between a row and its account shows up here as a
            # residual and nowhere else.
            "unexplained": worth["unexplained"],
            "warnings": worth["warnings"] + spend_warnings,
            "base": (base, spend_base),
            "cash flow": [(p["date"], p["income"], p["expense"], p["net"]) for p in flows],
            # By name, not by key: a row's key is an identity built from a category
            # id, so the two households are *supposed* to disagree about it.
            "spending": sorted((r["category_name"], r["total"]) for r in rows),
            "spending total": total,
            "transactions": count,
        }

    assert await reported(source) == await reported(target)


async def test_base_amount_is_recomputed_from_the_rates_that_travelled(household_factory):
    """``base_amount`` is a cache, so the document carries the rates instead.

    The assertion is that the number comes back *equal*, which is the only
    observable difference between exporting the cache and recomputing it: a
    document without the rates would import the row with ``base_amount`` NULL and
    a report would show a hole.
    """
    source = await household_factory()
    target = await household_factory()
    async with scoped_session(household_id=source) as s:
        seeded = await _seed(s, source)
        aud = Account(household_id=source, name="Sydney", type="depository",
                      currency="AUD", current_balance=D("0.0000"), balance_date=ON,
                      is_asset=True, owner_id=seeded["owner"].id)
        s.add(aud)
        await s.flush()
        await ledger.upsert_fx_rate(s, source, base_ccy="USD", quote_ccy="AUD",
                                    rate_date=ON, rate=D("1.52500000"))
        txn = await txns.create_transaction(
            s, source,
            TransactionCreate(account_id=aud.id, amount=D("-15.2500"),
                              transacted_at=NOON, description="Sydney row"),
            source="manual",
        )
        await s.flush()
        assert txn.base_amount == D("-10.0000")

    document, _ = await _roundtrip(source, target)

    async with scoped_session(household_id=target) as s:
        aud = (await s.execute(select(Account).where(Account.name == "Sydney"))).scalar_one()
        row = (
            await s.execute(
                select(Transaction).where(Transaction.account_id == aud.id)
            )
        ).scalar_one()
        assert row.amount == D("-15.2500")
        # 15.25 AUD ÷ 1.525 = 10 USD — the same number the source computed, from
        # the rate that travelled rather than from the target's rate table, which
        # is empty of AUD.
        assert row.base_amount == D("-10.0000")
        assert row.fx_rate_date == ON

        # The document carries no base_amount key at all: it is not exportable
        # data, and a key that shipped as null would read as one.
        assert all("base_amount" not in t for t in document["transactions"])


async def test_importing_the_same_document_twice_creates_nothing(household_factory):
    """The idempotence bar. The second import must be a no-op in *every* table."""
    source = await household_factory()
    target = await household_factory()
    async with scoped_session(household_id=source) as s:
        await _seed(s, source)

    async with scoped_session(household_id=source) as s:
        raw = portability.dumps(await _export(s, source))

    async with scoped_session(household_id=target) as s:
        first = await portability.import_document(s, target, raw)
    async with scoped_session(household_id=target) as s:
        second = await portability.import_document(s, target, raw)

    assert first.created["transactions"] == 3
    assert second.created == {}, second.created
    assert second.matched["transactions"] == 3
    assert second.matched["accounts"] == 2
    assert second.matched["categories"] == 1
    assert second.matched["rules"] == 1
    assert second.matched["holdings"] == 1

    async with scoped_session(household_id=target) as s:
        # Counts written out table by table rather than looped over the models, so
        # a table added to the document has to be added here deliberately.
        # Two accounts and two owners: the source's Alex and Checking arrive, and
        # the target's own Shared owner is the second of each pair's kind.
        assert len((await s.execute(select(Account))).scalars().all()) == 2
        assert len((await s.execute(select(Owner))).scalars().all()) == 2
        assert len((await s.execute(select(Category))).scalars().all()) == 1
        assert len((await s.execute(select(Tag))).scalars().all()) == 1
        assert len((await s.execute(select(Transaction))).scalars().all()) == 3
        assert len((await s.execute(select(TransactionSplit))).scalars().all()) == 2
        assert len((await s.execute(select(TransactionTag))).scalars().all()) == 1
        assert len((await s.execute(select(Security))).scalars().all()) == 1
        assert len((await s.execute(select(SecurityPrice))).scalars().all()) == 1
        assert len((await s.execute(select(Holding))).scalars().all()) == 1
        assert len((await s.execute(select(Rule))).scalars().all()) == 1


# ---- what must not be in the document ---------------------------------------


async def test_no_export_byte_contains_a_credential(household_factory):
    """ADR-0036's hard requirement, tested against the plaintext and the ciphertext.

    The credential is written through a real ``SecretBox``, so the ciphertext in
    the database is what production would hold. Both spellings are searched for in
    the exported bytes: a future edit that added ``access_url_encrypted`` to a
    field list fails on the ciphertext, and one that decrypted the column to
    "helpfully" re-serialize it fails on the plaintext.
    """
    box = SecretBox("a-test-secret-key")
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await _seed(s, hh)
        connection = AccountConnection(
            household_id=hh, provider="simplefin", org_name="Test Mutual",
            access_url_encrypted=box.encrypt(ACCESS_URL), status="ok",
                     )
        s.add(connection)
        await s.flush()
        ciphertext = connection.access_url_encrypted
        document = await _export(s, hh)

    raw = portability.dumps(document)
    assert ciphertext.encode() not in raw
    assert ACCESS_URL.encode() not in raw
    # And the credential is not in the structured document under any key.
    assert "access_url" not in raw.decode()

    # The connection itself did travel — metadata is not the secret.
    assert [c["org_name"] for c in document["connections"]] == ["Test Mutual"]
    assert set(document["connections"][0]) == set(portability._CONNECTION_FIELDS)


async def test_a_connection_imports_broken_and_says_so(household_factory):
    """No credential means it cannot sync, and ``auth_error`` is that state.

    The alternative — landing it ``ok`` — would leave a connection that reports
    healthy and fails on the next cron, which is the state the admin panel exists
    to make impossible.
    """
    source = await household_factory()
    target = await household_factory()
    box = SecretBox("a-test-secret-key")
    async with scoped_session(household_id=source) as s:
        seeded = await _seed(s, source)
        connection = AccountConnection(household_id=source, provider="simplefin",
                                       org_name="Test Mutual", status="ok",
                                       access_url_encrypted=box.encrypt(ACCESS_URL))
        s.add(connection)
        await s.flush()
        # The synced shape: the account hangs off the connection, so an import that
        # dropped the link would leave an account pointing at nothing.
        seeded["checking"].connection_id = connection.id
        await s.flush()

    await _roundtrip(source, target)

    async with scoped_session(household_id=target) as s:
        connection = (await s.execute(select(AccountConnection))).scalar_one()
        assert connection.status == "auth_error"
        assert connection.access_url_encrypted is None
        assert connection.last_error == portability.CREDENTIAL_NOT_EXPORTED
        assert connection.org_name == "Test Mutual"
        # The account still points at it, so reconnecting restores the link
        # rather than leaving an orphan account behind.
        account = (await s.execute(select(Account).where(Account.name == "Checking"))).scalar_one()
        assert account.connection_id == connection.id
        assert seeded["checking"].id != account.id


# ---- the document's own contract --------------------------------------------


async def test_the_document_refuses_the_shapes_it_cannot_read(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        async def refuses(raw: bytes, fragment: str):
            with pytest.raises(ledger.LedgerError) as caught:
                await portability.import_document(s, hh, raw)
            assert fragment in str(caught.value), str(caught.value)

        await refuses(b"not json at all", "Not a JSON document")
        await refuses(b'["a", "list"]', "expected a JSON object")
        await refuses(json.dumps({"format": "something.else", "version": 1}).encode(),
                      "Not a MetalMark export")
        # A future version is refused rather than partially applied.
        await refuses(json.dumps({"format": portability.FORMAT, "version": 99}).encode(),
                      "reads version 1")


async def test_money_must_be_a_string_not_a_number(household_factory):
    """ADR-0005 at the format boundary. A JSON number is refused *by name*.

    ``Decimal(str(1200.0))`` would accept this document and store ``1200.0``,
    which happens to be right — the point is that it is right by luck, and that
    ``0.1 + 0.2`` is where the luck runs out.
    """
    hh = await household_factory()
    document = {
        "format": portability.FORMAT,
        "version": 1,
        "owners": [{"id": str(uuid.uuid4()), "name": "Alex", "kind": "person", "sort": 0}],
        "accounts": [{
            "id": str(uuid.uuid4()), "name": "Checking", "type": "depository",
            "currency": "USD", "is_asset": True, "current_balance": 1200.0,
        }],
    }
    async with scoped_session(household_id=hh) as s:
        with pytest.raises(ledger.LedgerError) as caught:
            await portability.import_document(s, hh, json.dumps(document).encode())
    message = str(caught.value)
    assert "accounts[0].current_balance" in message
    assert "decimal string" in message


async def test_a_dangling_reference_is_refused_by_name(household_factory):
    """The failure a truncated document produces, named rather than left to the FK."""
    hh = await household_factory()
    document = {
        "format": portability.FORMAT,
        "version": 1,
        "accounts": [{
            "id": str(uuid.uuid4()), "name": "Checking", "type": "depository",
            "currency": "USD", "is_asset": True, "current_balance": "0.0000",
        }],
        "transactions": [{
            "id": str(uuid.uuid4()), "account_id": str(uuid.uuid4()),
            "transacted_at": NOON.isoformat(), "amount": "-1.0000", "currency": "USD",
        }],
    }
    async with scoped_session(household_id=hh) as s:
        with pytest.raises(ledger.LedgerError) as caught:
            await portability.import_document(s, hh, json.dumps(document).encode())
    assert "transactions[0]:" in str(caught.value)
    assert "does not define" in str(caught.value)


async def test_a_rule_pointing_at_a_deleted_category_warns_rather_than_failing(
    household_factory,
):
    """A dangling *optional* reference is a warning, not an error.

    A live household deletes categories; a rule left pointing at one is a real
    document, and refusing the file would make an ordinary export un-importable.
    """
    source = await household_factory()
    target = await household_factory()
    async with scoped_session(household_id=source) as s:
        await _seed(s, source)
        raw = portability.dumps(await _export(s, source))

    document = json.loads(raw)
    document["categories"] = []  # the category the rule points at is gone
    async with scoped_session(household_id=target) as s:
        result = await portability.import_document(s, target, json.dumps(document).encode())

    assert result.warnings, "a dropped reference must be recorded"
    assert any("category" in w for w in result.warnings)
    async with scoped_session(household_id=target) as s:
        rule = (await s.execute(select(Rule))).scalar_one()
        assert rule.actions["set_category_id"] is None
        # The transaction that referenced it landed uncategorized rather than
        # not at all.
        rows = (await s.execute(select(Transaction))).scalars().all()
        assert len(rows) == 3
        assert all(r.category_id is None for r in rows)


async def test_an_export_is_household_scoped(household_factory):
    """RLS is what makes this true, and the test says so out loud.

    Both households get a "Checking" account; neither document may contain the
    other's. The names collide on purpose, so a leak would show up as a count
    rather than needing a distinctive string to search for.
    """
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        await _seed(s, a)
    async with scoped_session(household_id=b) as s:
        owner = Owner(household_id=b, name="Bea", kind="person")
        s.add(owner)
        await s.flush()
        s.add(Account(household_id=b, name="Savings", type="depository", currency="USD",
                      current_balance=D("1.0000"), is_asset=True, owner_id=owner.id))
        await s.flush()

    async with scoped_session(household_id=a) as s:
        doc_a = await _export(s, a)
    async with scoped_session(household_id=b) as s:
        doc_b = await _export(s, b)

    # Both households share the base currency and the rate table is global, so
    # compare the entity lists rather than the whole document.
    # B's own rows and nothing of A's. Written out as numbers rather than a rule,
    # because the point is the count, not the shape of the expectation: B has one
    # account (Savings) and two owners (its Shared, plus Bea).
    assert {key: len(doc_b[key]) for key in (
        "accounts", "transactions", "owners", "categories", "rules", "tags",
        "securities", "holdings")} == {
        "accounts": 1, "transactions": 0, "owners": 2, "categories": 0,
        "rules": 0, "tags": 0, "securities": 0, "holdings": 0,
    }
    assert {a_["name"] for a_ in doc_a["accounts"]} == {"Checking", "Brokerage"}
    assert {row["name"] for row in doc_a["owners"]} == {"Alex", "Shared"}


# ---- the CSV ----------------------------------------------------------------


async def test_the_csv_export_is_a_file_the_csv_importer_can_read(household_factory):
    """The two modules' contracts meet, and this is where that is checked.

    Not "the CSV parses" — the importer's own ``suggest_mapping`` must map every
    column with no human input, and a re-import into the same account must skip
    every row through the *existing* dedupe rule. A new format would have been
    easy; a file that goes back in through the path that is already tested is the
    point.

    The rows are imported *first*, and that ordering is the test. A row typed into
    the UI carries no ``import_hash`` — ``ledger`` never writes one, because
    nothing about a person entering a row asserts that it is the same as any other
    row — so there is no digest for a re-import to reproduce and the round trip
    would pass or fail for reasons that have nothing to do with the CSV.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        seeded = await _seed(s, hh)
        account = Account(household_id=hh, name="Imported", type="depository",
                          currency="USD", current_balance=D("0.0000"), is_asset=True,
                          owner_id=seeded["owner"].id, is_manual=True)
        s.add(account)
        await s.flush()
        account_id = account.id

    async def csv_of(account_id):
        async with scoped_session(household_id=hh) as s:
            rows = list((await s.execute(
                select(Transaction)
                .where(Transaction.account_id == account_id)
                .order_by(Transaction.transacted_at, Transaction.id)
            )).scalars().all())
            owners = dict((await s.execute(select(Owner.id, Owner.name))).all())
            categories = dict((await s.execute(select(Category.id, Category.name))).all())
            return portability.transactions_csv(rows, owners, categories)

    # Two identical rows on purpose: the ordinal in the digest is the only thing
    # that keeps them apart, so a CSV that lost their order would collapse them.
    source = (
        b"date,amount,description,category,owner,notes\r\n"
        b"2026-03-05,-9.9900,Coffee,,,\r\n"
        b"2026-03-05,-9.9900,Coffee,,,\r\n"
        b"2026-03-06,-42.5000,Payroll,,,\r\n"
    )

    preview = imports_svc.preview_csv(source)
    assert preview.headers == list(portability.CSV_HEADERS)
    # Every column maps, with no leftovers for a human to assign.
    assert set(preview.suggested.values()) == set(portability.CSV_HEADERS)
    assert None not in preview.suggested.values()

    async with scoped_session(household_id=hh) as s:
        first = await imports_svc.commit_csv(s, hh, raw=source,
                                             mapping=preview.suggested,
                                             account_id=account_id)
    assert first.inserted == 3
    assert first.errors == []

    # Second trip: the rows now carry digests, and the exported file reproduces
    # them — same order, same ordinals, same hashes.
    raw = await csv_of(account_id)
    preview = imports_svc.preview_csv(raw)
    async with scoped_session(household_id=hh) as s:
        second = await imports_svc.commit_csv(s, hh, raw=raw,
                                              mapping=preview.suggested,
                                              account_id=account_id)
    assert second.inserted == 0
    assert second.skipped == 3
    assert second.errors == []


async def test_a_hand_typed_row_is_matched_by_position_not_by_digest(household_factory):
    """The other half of the same fact, on the document side.

    ``ledger`` writes no ``import_hash``, so a document's digest lookup cannot see
    a row the user typed — and importing an export back into the household it came
    from is the restore path a user is *most* likely to take. The fallback adopts
    identical hash-less rows by position, which is the same ordinal rule the digest
    itself is built on.

    Position is the fallback and not the rule, so the second import must find the
    rows it created by their digests: were adoption the primary path, a deleted row
    in the middle of a run of identical ones would shift every ordinate after it.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await _seed(s, hh)
        raw = portability.dumps(await _export(s, hh))

    async with scoped_session(household_id=hh) as s:
        first = await portability.import_document(s, hh, raw)
    # Nothing was created: every row was already here, typed rather than imported.
    assert first.created == {}
    assert first.matched["transactions"] == 3

    async with scoped_session(household_id=hh) as s:
        second = await portability.import_document(s, hh, raw)
    assert second.created == {}
    assert second.matched["transactions"] == 3

    async with scoped_session(household_id=hh) as s:
        rows = (await s.execute(select(Transaction))).scalars().all()
        assert len(rows) == 3
        # And the provider row was found by its external_id, which is the key the
        # position fallback deliberately refuses to consider.
        assert len([r for r in rows if r.external_id == "sf-1234"]) == 1


def test_the_two_readers_compute_the_same_digest():
    """``portability._digest`` and ``imports.import_hash`` must not drift.

    They are written out twice on purpose — one feeds on a document's ordinals and
    the other on a file's — but they hash the same payload, and this is the case
    that fails if either spelling changes.
    """
    account_id = uuid.uuid4()
    for when, amount, description, ordinal in (
        (NOON, D("-9.9900"), "Manual row", 0),
        (NOON, D("-9.9900"), "Manual row", 1),
        (NOON, D("0.0000"), None, 0),
    ):
        assert portability._digest(account_id, when, amount, description, ordinal) == \
            imports_svc.import_hash(account_id, when, amount, description, ordinal)


async def test_the_csv_is_one_account_because_the_importer_is(household_factory):
    """The route's ``account_id`` is required, and the module says why.

    Asserted at the service boundary: a CSV holds the rows it was handed, so the
    guarantee is made by the caller — and this is the test that fails if someone
    later "helpfully" makes the parameter optional and passes every row.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        seeded = await _seed(s, hh)
        rows = list((await s.execute(
            select(Transaction).where(Transaction.account_id == seeded["checking"].id)
        )).scalars().all())
        raw = portability.transactions_csv(rows, {}, {})
    # Header plus one line per row, and no line for the brokerage account's
    # transactions (there are none — investment history is not this table).
    assert raw.decode("utf-8").count("\r\n") == len(rows) + 1
    assert raw.startswith(portability.CSV_BOM.encode("utf-8"))


def test_a_hand_typed_row_exports_its_merchant_as_the_payee():
    """The payee column of a file nobody typed a description into.

    ``description`` is the provider's raw string, so a row entered in the app has
    a merchant and ``None`` there. Without the fallback the export of a
    hand-kept ledger is a file of dates and amounts with no payee on any line —
    and it re-imports clean, so no count anywhere would report it.

    Built here rather than through the services because the subject is one
    expression in one function, and a database would only add ways for the test
    to be about something else.
    """
    def row(merchant, description):
        return Transaction(household_id=uuid.uuid4(), account_id=uuid.uuid4(),
                           transacted_at=NOON, amount=D("-9.9900"), currency="USD",
                           merchant=merchant, description=description)

    raw = portability.transactions_csv([
        row("Coffee Shop", None),              # typed in the app
        row("Coffee Shop", "SQ *COFFEE 1234"),  # synced, and a rule renamed it
    ], {}, {})
    lines = raw.decode("utf-8-sig").strip().split("\r\n")

    assert lines[1].split(",")[2] == "Coffee Shop"
    # The other row keeps the provider's string: a rule's merchant is a *second*
    # name for a row, and this file already prints one of them.
    assert lines[2].split(",")[2] == "SQ *COFFEE 1234"


async def test_a_document_imports_even_when_the_household_is_not_empty(household_factory):
    """The merge case, which is the one a restore drill does not cover.

    A household that already holds an "Alex" owner and a "Groceries" category in
    the same group must not gain a second one — the natural keys exist to make the
    merge land on the rows that are already there.
    """
    source = await household_factory()
    target = await household_factory()
    async with scoped_session(household_id=source) as s:
        await _seed(s, source)
        raw = portability.dumps(await _export(s, source))

    async with scoped_session(household_id=target) as s:
        existing = (await s.execute(select(Owner).where(Owner.kind == "person"))).scalars().all()
        assert existing == []
    async with scoped_session(household_id=source) as s:
        source_owner = (await s.execute(
            select(Owner).where(Owner.name == "Alex")
        )).scalar_one()
        source_owner_id = source_owner.id

    async with scoped_session(household_id=target) as s:
        # A pre-existing Alex of the target's own, created through the service so
        # the household's Shared owner is handled the way production handles it.
        await owners_svc.create_owner(s, target, name="alex")
        await s.flush()
        result = await portability.import_document(s, target, raw)

    # Recorded as absent rather than as zero: the maps hold what happened, so an
    # entity missing from ``created`` is one that was not created.
    assert "owners" not in result.created, result.created
    assert result.matched["owners"] == 2  # Alex and Shared, both already here
    async with scoped_session(household_id=target) as s:
        owners = (await s.execute(select(Owner))).scalars().all()
        # One Alex, not two: matched case-insensitively, which is the index's rule —
        # and matched means the row that was already here is kept, name and all, so
        # the document cannot rename an owner out from under its own history.
        assert len(owners) == 2
        assert {o.name.lower() for o in owners} == {"alex", "shared"}
        assert all(o.id != source_owner_id for o in owners)
        # And the account that pointed at the source's Alex points at this one.
        account = (await s.execute(select(Account).where(Account.name == "Checking"))).scalar_one()
        alex = next(o for o in owners if o.name.lower() == "alex")
        assert account.owner_id == alex.id


async def test_an_investment_accounts_history_travels_as_events(household_factory):
    """``investment_transactions``, deduped by their own ``import_hash``.

    They are not ``transactions`` (ADR-0033), so they are a separate section with a
    separate reader — and this is the test that would notice the section being
    dropped from the document.
    """
    source = await household_factory()
    target = await household_factory()
    async with scoped_session(household_id=source) as s:
        seeded = await _seed(s, source)
        await s.flush()
        s.add(InvestmentTransaction(
            household_id=source, account_id=seeded["broker"].id,
            security_id=seeded["security"].id, type="buy", trade_date=ON,
            quantity=D("2.00000000"), price=D("200.00000000"), amount=D("-400.0000"),
            currency="USD", description="bought", source="manual",
        ))
        await s.flush()
        raw = portability.dumps(await _export(s, source))

    async with scoped_session(household_id=target) as s:
        first = await portability.import_document(s, target, raw)
    async with scoped_session(household_id=target) as s:
        second = await portability.import_document(s, target, raw)

    assert first.created["investment_transactions"] == 1
    assert second.created == {}
    assert second.matched["investment_transactions"] == 1
    async with scoped_session(household_id=target) as s:
        event = (await s.execute(select(InvestmentTransaction))).scalar_one()
        assert event.type == "buy"
        assert event.quantity == D("2.00000000")
        assert event.price == D("200.00000000")
        assert event.amount == D("-400.0000")
        # Remapped onto the target's own security, which is what the holding above
        # was filed against too.
        security = (await s.execute(select(Security).where(Security.ticker == "VTI"))).scalar_one()
        assert event.security_id == security.id
