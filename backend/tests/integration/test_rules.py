"""Rules engine (WS-R): the evaluator, provenance, priority, and idempotency.

Two of these are the acceptance bar PLAN.md names for this workstream: a rule
never overwrites a field a human set (ADR-0007/0019 — the reason the vertical
exists at all), and "apply to existing" run twice changes nothing the second
time. The rest pin what those depend on: a closed condition/action key set, a
total priority order, and a chunked walk that neither skips nor double-counts.

The last section drives the same engine over HTTP, because three of its promises
live only in the request path: the CSRF/role gates, the 422 for a regex that will
not compile, and route matching.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import scoped_session
from app.deps import SESSION_COOKIE
from app.models import TransactionTag
from app.schemas.ledger import AccountCreate
from app.schemas.rules import (
    ACTION_KEYS,
    CONDITION_KEYS,
    RuleActions,
    RuleConditions,
    RuleCreate,
    RuleUpdate,
)
from app.schemas.transactions import TransactionCreate, TransactionUpdate
from app.services import ledger, owners, rules
from app.services import transactions as txns
from app.services.errors import LedgerError

pytestmark = pytest.mark.integration

D = Decimal


def _dt(y, m, d, hour=0):
    return datetime(y, m, d, hour, tzinfo=UTC)


# ---- Building blocks -------------------------------------------------------


async def _account(session, household_id, name="Checking", owner_id=None):
    return await ledger.create_account(
        session, household_id,
        AccountCreate(name=name, type="depository", currency="USD", owner_id=owner_id),
    )


async def _category(session, household_id, name="Groceries"):
    group = await ledger.create_category_group(session, household_id, "Expense", "expense", 0)
    return await ledger.create_category(session, household_id, group.id, name, None, None, 0)


async def _txn(session, household_id, account_id, amount, **kw):
    return await txns.create_transaction(
        session, household_id,
        TransactionCreate(
            account_id=account_id, amount=D(amount),
            transacted_at=kw.pop("transacted_at", _dt(2026, 1, 5)), **kw,
        ),
    )


async def _rule(session, household_id, *, name="Rule", conditions=None, actions=None,
                priority=100, enabled=True):
    return await rules.create_rule(
        session, household_id,
        RuleCreate(
            name=name, priority=priority, enabled=enabled,
            conditions=RuleConditions(**(conditions or {})),
            actions=RuleActions(**(actions or {})),
        ),
    )


async def _tagged_ids(session, tag_id) -> set[uuid.UUID]:
    return set(
        (
            await session.execute(
                select(TransactionTag.transaction_id).where(TransactionTag.tag_id == tag_id)
            )
        ).scalars().all()
    )


async def _matched(session, household_id, **conditions) -> set[uuid.UUID]:
    """Which transactions a rule with these conditions matches.

    ``apply`` reports counts, but every matching test wants the identity of the
    rows, so the probe rule tags what it hits and is deleted again — a tag is the
    only action that leaves a per-row trace without disturbing a field some other
    case asserts on.
    """
    tag = await ledger.create_tag(session, household_id, f"probe-{uuid.uuid4().hex[:8]}", None)
    rule = await _rule(session, household_id, conditions=conditions,
                       actions={"add_tag_ids": [tag.id]})
    await rules.apply(session, household_id)
    await rules.delete_rule(session, rule.id)
    return await _tagged_ids(session, tag.id)


# ---- The closed key set ----------------------------------------------------


def test_the_key_sets_are_exactly_the_documented_ones():
    """The exported sets are the contract, not a copy of it — a key added to one
    without the other fails here instead of drifting."""
    assert set(RuleConditions.model_fields) == set(CONDITION_KEYS)
    assert set(RuleActions.model_fields) == set(ACTION_KEYS)


def test_an_unknown_condition_key_is_rejected_not_ignored():
    """A misspelled key would otherwise store cleanly and then match everything."""
    with pytest.raises(ValidationError):
        RuleConditions(merchant_contain="AMZN")  # typo: no trailing "s"
    # And through the body the API actually receives, which is what makes it a 422.
    with pytest.raises(ValidationError):
        RuleCreate.model_validate({"name": "Amazon", "conditions": {"merchant_contain": "AMZN"}})


def test_an_unknown_action_key_is_rejected():
    with pytest.raises(ValidationError):
        RuleActions(set_catgory_id=uuid.uuid4())
    # `split` is the Phase-2 auto-split action: not built, so not accepted — and
    # accepting-then-dropping it would be the silent no-op the closed set prevents.
    with pytest.raises(ValidationError):
        RuleCreate.model_validate({"name": "x", "actions": {"split": [{"pct": "50"}]}})


def test_a_bad_regex_is_rejected_when_the_rule_is_written():
    """A 422 at save time, not an exception halfway through an apply — by which
    point the rules ahead of it have already been written."""
    with pytest.raises(ValidationError):
        RuleConditions(description_regex="(unclosed")
    assert RuleConditions(description_regex="AMZN.*").description_regex == "AMZN.*"


def test_empty_lists_and_inverted_bounds_are_rejected():
    """Each of these makes a rule that looks alive and can never do anything."""
    with pytest.raises(ValidationError):
        RuleConditions(account_ids=[])
    with pytest.raises(ValidationError):
        RuleActions(add_tag_ids=[])
    with pytest.raises(ValidationError):
        RuleConditions(amount_min=D("-10"), amount_max=D("-50"))


def test_rule_update_cannot_null_a_required_field():
    with pytest.raises(ValidationError):
        RuleUpdate(name=None)
    with pytest.raises(ValidationError):
        RuleUpdate(priority=None)
    with pytest.raises(ValidationError):
        RuleUpdate(enabled=None)
    # Absent is still fine: it means "no change".
    assert RuleUpdate(name="x").priority is None


# ---- CRUD ------------------------------------------------------------------


async def test_rule_crud_round_trip(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        cat = await _category(s, hh, "Amazon")
        rule = await _rule(
            s, hh, name="Amazon is shopping",
            conditions={"merchant_contains": "amzn", "amount_max": D("-50.00")},
            actions={"set_category_id": cat.id, "mark_reviewed": True},
            priority=10,
        )

        fetched = await rules.get_rule(s, rule.id)
        assert fetched.name == "Amazon is shopping"
        assert fetched.priority == 10 and fetched.enabled is True
        # Stored as a decimal string, read back as a Decimal — never a float
        # (ADR-0005). JSONB has no number type that keeps cents.
        assert fetched.conditions["amount_max"] == "-50.00"
        assert RuleConditions.model_validate(fetched.conditions).amount_max == D("-50.00")
        assert fetched.actions["set_category_id"] == str(cat.id)

        updated = await rules.update_rule(s, rule.id, RuleUpdate(priority=5, enabled=False))
        assert (updated.priority, updated.enabled) == (5, False)
        assert updated.name == "Amazon is shopping"  # absent: untouched

        await rules.delete_rule(s, rule.id)
        with pytest.raises(LedgerError) as exc:
            await rules.get_rule(s, rule.id)
    assert exc.value.status == 404


async def test_clearing_conditions_and_actions_is_explicit_null(household_factory):
    """PATCH semantics: absent = unchanged, explicit null = clear."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        rule = await _rule(s, hh, conditions={"direction": "out"}, actions={"set_hidden": True})

        cleared = await rules.update_rule(s, rule.id, RuleUpdate(conditions=None))
        assert cleared.conditions == {}
        assert cleared.actions == {"set_hidden": True}  # not touched

        cleared = await rules.update_rule(s, rule.id, RuleUpdate(actions=None))
        assert cleared.actions == {}

        renamed = await rules.update_rule(s, rule.id, RuleUpdate(name="Renamed"))
        assert renamed.conditions == {} and renamed.actions == {}
        assert renamed.name == "Renamed"


async def test_rules_are_listed_in_priority_order(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        await _rule(s, hh, name="third", priority=30)
        await _rule(s, hh, name="first", priority=10)
        await _rule(s, hh, name="second", priority=20)
        assert [r.name for r in await rules.list_rules(s)] == ["first", "second", "third"]


async def test_get_rule_hides_another_households_rule(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        rule = await _rule(s, a, name="A's rule")
    async with scoped_session(household_id=b) as s:
        with pytest.raises(LedgerError) as exc:
            await rules.get_rule(s, rule.id)
        assert await rules.list_rules(s) == []  # RLS is the only scoping in play
    assert exc.value.status == 404


async def test_create_rule_rejects_a_foreign_owner_category_or_tag(household_factory):
    """An action's ids have to exist *here*: a foreign key would accept another
    household's row (the constraint knows nothing about RLS), so the check is
    explicit and answers 404 like every other foreign id in the ledger."""
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        foreign_owner = await owners.create_owner(s, a, name="Alex")
        foreign_cat = await _category(s, a, "A's category")
        foreign_tag = await ledger.create_tag(s, a, "A's tag", None)
    async with scoped_session(household_id=b) as s:
        for actions in (
            {"set_owner_id": foreign_owner.id},
            {"set_category_id": foreign_cat.id},
            {"add_tag_ids": [foreign_tag.id]},
        ):
            with pytest.raises(LedgerError) as exc:
                await _rule(s, b, actions=actions)
            assert exc.value.status == 404


async def test_update_rule_rejects_a_foreign_owner_but_still_allows_a_disable(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        foreign_owner = await owners.create_owner(s, a, name="Alex")
    async with scoped_session(household_id=b) as s:
        rule = await _rule(s, b, actions={"set_hidden": True})
        with pytest.raises(LedgerError) as exc:
            await rules.update_rule(
                s, rule.id, RuleUpdate(actions=RuleActions(set_owner_id=foreign_owner.id))
            )
        assert exc.value.status == 404
        # A patch that touches neither half is not re-validated: that is what lets
        # a rule be renamed or switched off even once what it points at is gone.
        disabled = await rules.update_rule(s, rule.id, RuleUpdate(enabled=False))
        assert disabled.enabled is False
        assert disabled.actions == {"set_hidden": True}


# ---- Matching --------------------------------------------------------------


@pytest.fixture
async def book(household_factory):
    """A household with two accounts and five transactions between them.

    Four of the five carry a merchant and two carry a description, so the NULL
    cases have rows of their own to be false about.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        checking = await _account(s, hh, "Checking")
        card = await _account(s, hh, "Card")
        dining = await _category(s, hh, "Dining")
        coffee = await _txn(s, hh, card.id, "-4.50", merchant="Blue Bottle Coffee")
        amazon = await _txn(s, hh, card.id, "-120.00", merchant="AMZN Mktp US",
                            description="AMZN Mktp US*2H4L1")
        salary = await _txn(s, hh, checking.id, "3000.00", merchant="Acme Payroll",
                            description="DIRECT DEPOSIT ACME")
        unnamed = await _txn(s, hh, card.id, "-9.99")  # no merchant, no description
        pending = await _txn(s, hh, card.id, "-30.00", merchant="Shell", is_pending=True)
        await txns.update_transaction(s, hh, coffee.id, TransactionUpdate(category_id=dining.id))
    return {
        "hh": hh, "checking": checking, "card": card, "dining": dining,
        "coffee": coffee, "amazon": amazon, "salary": salary,
        "unnamed": unnamed, "pending": pending,
    }


async def test_merchant_contains_is_case_insensitive(book):
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], merchant_contains="amzn") == {f["amazon"].id}
        assert await _matched(s, f["hh"], merchant_contains="BLUE bottle") == {f["coffee"].id}


async def test_a_condition_on_a_null_column_matches_nothing(book):
    """ "contains e" is false for a row with no merchant — not vacuously true."""
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], merchant_contains="e") == {
            f["coffee"].id, f["salary"].id, f["pending"].id,
        }
        # Same rule for the regex: "." matches any character, but not nothing.
        assert await _matched(s, f["hh"], description_regex=".") == {
            f["amazon"].id, f["salary"].id,
        }


async def test_description_regex_uses_search_not_fullmatch(book):
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], description_regex="^DIRECT DEPOSIT") == {
            f["salary"].id
        }
        assert await _matched(s, f["hh"], description_regex="2H4L1$") == {f["amazon"].id}
        assert await _matched(s, f["hh"], description_regex="^Mktp") == set()


async def test_amount_bounds_are_on_the_signed_amount(book):
    """Expenses are negative, so "spending over $100" is amount_max = -100."""
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], amount_max=D("-100")) == {f["amazon"].id}
        assert await _matched(s, f["hh"], amount_min=D("-5")) == {
            f["coffee"].id, f["salary"].id,
        }
        assert await _matched(s, f["hh"], amount_min=D("-120"), amount_max=D("-9.99")) == {
            f["amazon"].id, f["unnamed"].id, f["pending"].id,
        }


async def test_direction_splits_money_in_from_money_out(book):
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], direction="in") == {f["salary"].id}
        assert await _matched(s, f["hh"], direction="out") == {
            f["coffee"].id, f["amazon"].id, f["unnamed"].id, f["pending"].id,
        }


async def test_account_ids_category_id_and_is_pending(book):
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], account_ids=[f["card"].id]) == {
            f["coffee"].id, f["amazon"].id, f["unnamed"].id, f["pending"].id,
        }
        assert await _matched(s, f["hh"], account_ids=[f["checking"].id, f["card"].id]) == {
            f["coffee"].id, f["amazon"].id, f["salary"].id, f["unnamed"].id, f["pending"].id,
        }
        assert await _matched(s, f["hh"], category_id=f["dining"].id) == {f["coffee"].id}
        assert await _matched(s, f["hh"], is_pending=True) == {f["pending"].id}
        assert await _matched(s, f["hh"], is_pending=False) == {
            f["coffee"].id, f["amazon"].id, f["salary"].id, f["unnamed"].id,
        }


async def test_conditions_are_anded(book):
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"], merchant_contains="AMZN", direction="out") == {
            f["amazon"].id
        }
        # One condition failing is enough, whichever one it is.
        assert await _matched(s, f["hh"], merchant_contains="AMZN", direction="in") == set()
        assert await _matched(
            s, f["hh"], merchant_contains="AMZN", account_ids=[f["checking"].id]
        ) == set()


async def test_a_rule_with_no_conditions_matches_everything(book):
    f = book
    async with scoped_session(household_id=f["hh"]) as s:
        assert await _matched(s, f["hh"]) == {
            f["coffee"].id, f["amazon"].id, f["salary"].id, f["unnamed"].id, f["pending"].id,
        }


# ---- Actions and provenance ------------------------------------------------


async def test_a_rule_fills_a_blank_field_and_marks_it_rule(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Dining")
        txn = await _txn(s, hh, acct.id, "-12.00", merchant="Cafe")
        assert txn.category_id is None

        await _rule(s, hh, actions={"set_category_id": cat.id})
        await rules.apply(s, hh)
        await s.refresh(txn)

        assert txn.category_id == cat.id
        assert txn.field_sources["category"] == "rule"


async def test_a_rule_never_overwrites_a_field_a_human_set(household_factory):
    """The acceptance bar for this workstream (ADR-0007/0019).

    A human categorized the charge; a rule wants a different category. The rule
    loses, and the provenance stays ``user`` so the *next* rule loses too.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        human = await _category(s, hh, "Human's choice")
        rule_cat = await _category(s, hh, "Rule's choice")
        txn = await _txn(s, hh, acct.id, "-12.00", merchant="Cafe", category_id=human.id)
        assert txn.field_sources["category"] == "user"

        await _rule(s, hh, actions={"set_category_id": rule_cat.id})
        result = await rules.apply(s, hh)
        await s.refresh(txn)

        assert txn.category_id == human.id
        assert txn.field_sources["category"] == "user"
    # Matched, but nothing to change — the two counts the API reports differ.
    assert result.matched == 1
    assert result.updated == 0


async def test_a_rule_does_overwrite_a_provider_set_field(household_factory):
    """The other half of the precedence: an improved rule still beats the
    provider, which is what makes refining a rule worth doing."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        provider_cat = await _category(s, hh, "Provider's guess")
        rule_cat = await _category(s, hh, "Rule's answer")
        txn = await _txn(s, hh, acct.id, "-12.00")
        # Stand in for the sync writer (Phase 2) until it exists.
        txn.category_id = provider_cat.id
        txn.field_sources = {"category": "provider"}
        await s.flush()

        await _rule(s, hh, actions={"set_category_id": rule_cat.id})
        await rules.apply(s, hh)
        await s.refresh(txn)

        assert txn.category_id == rule_cat.id
        assert txn.field_sources["category"] == "rule"


async def test_a_rule_writes_owner_merchant_hidden_and_review_status(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.00", description="SQ *CORNER STORE 1234")
        # The merchant a provider would have supplied: it carries provenance, but
        # not ``user``, so a rename may still land (the test below covers the case
        # where a human typed it instead).
        txn.merchant = "SQ *CORNER STORE"
        txn.field_sources = {"merchant": "provider"}
        await s.flush()
        assert txn.review_status == "needs_review"

        await _rule(s, hh, actions={
            "set_owner_id": alex.id,
            "rename_merchant": "Corner Store",
            "set_hidden": True,
            "mark_reviewed": True,
        })
        changed = await rules.apply_to_transaction(s, hh, txn)
        await s.refresh(txn)

        assert txn.owner_id == alex.id
        assert txn.merchant == "Corner Store"
        assert txn.is_hidden is True
        assert txn.review_status == "reviewed"
    # The returned set names the provenance fields, not the columns.
    assert changed == {"owner", "merchant", "is_hidden", "review_status"}


async def test_a_rule_leaves_a_human_typed_merchant_alone(household_factory):
    """``create_transaction`` marks a merchant the caller supplied as ``user``, so
    a rename is a rule reaching for something a person wrote."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.00", merchant="Corner Store")

        await _rule(s, hh, actions={"rename_merchant": "Corner Store #42"})
        result = await rules.apply(s, hh)
        await s.refresh(txn)

        assert txn.merchant == "Corner Store"
    assert result.updated == 0


async def test_a_rule_leaves_a_human_set_owner_alone(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        alex = await owners.create_owner(s, hh, name="Alex")
        beth = await owners.create_owner(s, hh, name="Beth")
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.00", owner_id=beth.id)

        await _rule(s, hh, actions={"set_owner_id": alex.id})
        await rules.apply(s, hh)
        await s.refresh(txn)

        assert txn.owner_id == beth.id
        assert txn.field_sources["owner"] == "user"


async def test_add_tags_is_a_union_and_never_removes(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        existing = await ledger.create_tag(s, hh, "Home", None)
        added = await ledger.create_tag(s, hh, "Business", None)
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.00", tag_ids=[existing.id])

        await _rule(s, hh, actions={"add_tag_ids": [added.id]})
        result = await rules.apply(s, hh)

        # Both tags, and the human's is still there: "add" is a union, never a
        # replace, which is why it needs no provenance entry of its own.
        assert await _tagged_ids(s, existing.id) == {txn.id}
        assert await _tagged_ids(s, added.id) == {txn.id}
    assert result.updated == 1


async def test_add_tags_does_not_duplicate_on_a_second_run(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        tag = await ledger.create_tag(s, hh, "Business", None)
        acct = await _account(s, hh)
        await _txn(s, hh, acct.id, "-12.00")
        await _rule(s, hh, actions={"add_tag_ids": [tag.id]})
        first = await rules.apply(s, hh)
    async with scoped_session(household_id=hh) as s:
        second = await rules.apply(s, hh)
        tagged = await _tagged_ids(s, tag.id)
    assert first.updated == 1
    assert second.updated == 0
    assert len(tagged) == 1  # one row, one tag link — no duplicate insert


# ---- Priority --------------------------------------------------------------


async def test_a_lower_priority_number_runs_first(household_factory):
    """Two rules write the same field; the higher priority number is applied last,
    so its value is what remains."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.00")

        # Created first, but loses on priority: priority beats creation order.
        await _rule(s, hh, name="later", priority=20, actions={"set_hidden": False})
        await _rule(s, hh, name="earlier", priority=10, actions={"set_hidden": True})
        await rules.apply(s, hh)
        await s.refresh(txn)

        assert txn.is_hidden is False


async def test_equal_priority_falls_back_to_a_total_stable_order(household_factory):
    """Postgres ``now()`` is the *transaction* timestamp, so two rules written in
    one transaction tie on ``created_at`` exactly and the id decides. Arbitrary —
    but the same way on every run, which is what determinism has to mean."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        txn = await _txn(s, hh, acct.id, "-12.00")
        hide = await _rule(s, hh, name="hide", actions={"set_hidden": True})
        show = await _rule(s, hh, name="show", actions={"set_hidden": False})
        assert hide.created_at == show.created_at  # the tie is real

        await rules.apply(s, hh)
        await s.refresh(txn)
        first_run = txn.is_hidden
        await rules.apply(s, hh)
        await s.refresh(txn)

        # Apply order is (priority, created_at, id), so the larger id lands last.
        assert first_run is (hide.id > show.id)
        assert txn.is_hidden is first_run  # and it is the same on a second run


# ---- Applying --------------------------------------------------------------


async def test_apply_to_existing_is_idempotent(household_factory):
    """PLAN.md's R acceptance bar: the second run reports ``updated == 0``."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        for i in range(3):
            await _txn(s, hh, acct.id, f"-{10 + i}.00", merchant="WHOLE FOODS")
        await _rule(s, hh, conditions={"merchant_contains": "whole foods"},
                    actions={"set_category_id": cat.id, "mark_reviewed": True})

    async with scoped_session(household_id=hh) as s:
        first = await rules.apply(s, hh)
    async with scoped_session(household_id=hh) as s:
        second = await rules.apply(s, hh)

    assert first.matched == first.updated == 3
    assert second.matched == 3  # the conditions still match...
    assert second.updated == 0  # ...but every field is where the rules would put it


async def test_matched_and_updated_are_different_numbers(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        # Already categorized by hand, so only the second row can change.
        await _txn(s, hh, acct.id, "-10.00", merchant="WHOLE FOODS", category_id=cat.id)
        await _txn(s, hh, acct.id, "-20.00", merchant="WHOLE FOODS")
        await _rule(s, hh, conditions={"merchant_contains": "whole foods"},
                    actions={"set_category_id": cat.id})

        result = await rules.apply(s, hh)
    assert result.matched == 2
    assert result.updated == 1


async def test_apply_walks_every_row_across_chunks(household_factory, monkeypatch):
    """The walk is chunked (a household has 50k transactions); a cursor bug would
    show up as rows never visited rather than as an error."""
    monkeypatch.setattr(rules, "APPLY_CHUNK", 2)
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        ids = {(await _txn(s, hh, acct.id, f"-{i + 1}.00")).id for i in range(5)}
        await _rule(s, hh, actions={"set_category_id": cat.id})

        result = await rules.apply(s, hh)
        rows = (await txns.list_transactions(s))[0]
        assert len(ids) == 5
        assert result.matched == 5  # neither skipped nor double-counted
        assert result.updated == 5
        assert {r.id for r in rows} == ids
        assert all(r.category_id == cat.id for r in rows)


async def test_apply_skips_disabled_rules_and_needs_no_rules_at_all(household_factory):
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        txn = await _txn(s, hh, acct.id, "-10.00", merchant="WHOLE FOODS")

        assert (await rules.apply(s, hh)).model_dump() == {"matched": 0, "updated": 0}

        await _rule(s, hh, actions={"set_category_id": cat.id}, enabled=False)
        disabled = await rules.apply(s, hh)
        await s.refresh(txn)

        assert (disabled.matched, disabled.updated) == (0, 0)
        assert txn.category_id is None


async def test_apply_to_transaction_returns_the_fields_it_changed(household_factory):
    """The entry point reports the fields it changed, and only those."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        tag = await ledger.create_tag(s, hh, "Business", None)
        matching = await _txn(s, hh, acct.id, "-10.00", merchant="WHOLE FOODS")
        other = await _txn(s, hh, acct.id, "-5.00", merchant="Bus Ticket")
        # Written after the rows on purpose: ``create_transaction`` runs the
        # engine itself, so a rule written first would have been applied on
        # insert and this call would honestly report nothing to do.
        await _rule(s, hh, conditions={"merchant_contains": "whole foods"},
                    actions={"set_category_id": cat.id, "add_tag_ids": [tag.id]})

        changed = await rules.apply_to_transaction(s, hh, matching)
        untouched = await rules.apply_to_transaction(s, hh, other)
        # And nothing new to do the second time over the same row.
        again = await rules.apply_to_transaction(s, hh, matching)

    assert changed == {"category", "tags"}
    assert untouched == set()
    assert again == set()


async def test_preloaded_rules_bound_do_the_same_as_loading_per_row(household_factory):
    """A caller with many rows loads once — same answer, one set of rule queries."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        # Rows first, rule second: ``create_transaction`` runs the engine, so
        # the rule must not exist yet for this call to be the one that applies it.
        first = await _txn(s, hh, acct.id, "-10.00", merchant="WHOLE FOODS")
        second = await _txn(s, hh, acct.id, "-20.00", merchant="WHOLE FOODS")
        third = await _txn(s, hh, acct.id, "-30.00", merchant="Bus Ticket")
        await _rule(s, hh, conditions={"merchant_contains": "whole foods"},
                    actions={"set_category_id": cat.id})
        loaded = await rules.load_rules(s)
        assert loaded.skipped_ids == []

        changed = [await rules.apply_to_transaction(s, hh, t, loaded=loaded)
                   for t in (first, second, third)]
        await s.refresh(first)
        await s.refresh(second)

        assert changed == [{"category"}, {"category"}, set()]
        assert first.category_id == cat.id and second.category_id == cat.id


async def test_apply_to_transaction_refuses_a_row_from_another_household(household_factory):
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        acct = await _account(s, a)
        txn = await _txn(s, a, acct.id, "-10.00", merchant="WHOLE FOODS")
    async with scoped_session(household_id=b) as s:
        await _rule(s, b, actions={"set_hidden": True})
        with pytest.raises(LedgerError) as exc:
            await rules.apply_to_transaction(s, b, txn)
        assert exc.value.status == 404


async def test_apply_reports_a_rule_whose_category_was_deleted(household_factory):
    """A write that cannot be honored fails loudly. Skipping the rule quietly would
    leave an enabled rule that does nothing — the same live-looking no-op the
    closed key set exists to prevent — so the message names what to fix."""
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        cat = await _category(s, hh, "Groceries")
        await _txn(s, hh, acct.id, "-10.00", merchant="WHOLE FOODS")
        rule = await _rule(s, hh, actions={"set_category_id": cat.id})
        await ledger.delete_category(s, cat.id)

        with pytest.raises(LedgerError) as exc:
            await rules.apply(s, hh)
        assert exc.value.status == 404

        # And the broken reference does not make the rule undeletable.
        await rules.delete_rule(s, rule.id)


async def test_the_on_create_path_skips_a_stale_rule_instead_of_failing(household_factory):
    """The wedge this policy exists to prevent.

    A category a rule's action names can be deleted — nothing object-level stops
    it — and once the on-create hook runs rules, a strict check there would make
    *every* future insert 404 for a reason unrelated to the insert. So the create
    path skips the rule (and says so in the log); the rule stays visible and
    fixable through the API that still works.
    """
    hh = await household_factory()
    async with scoped_session(household_id=hh) as s:
        acct = await _account(s, hh)
        stale = await _category(s, hh, "Groceries")
        live = await _category(s, hh, "Dining")
        broken = await _rule(s, hh, name="stale", priority=10,
                             actions={"set_category_id": stale.id, "set_hidden": True})
        await _rule(s, hh, name="live", priority=20, actions={"set_category_id": live.id})
        await ledger.delete_category(s, stale.id)

        loaded = await rules.load_rules(s)
        assert loaded.skipped_ids == [broken.id]  # the stale one, and only it
        assert len(loaded.compiled) == 1  # the healthy rule still runs

        # The insert the household actually asked for goes through, and this is
        # the wedge itself: ``create_transaction`` runs the rules, so a strict
        # reference check on that path would make every future insert 404 for a
        # reason unrelated to the insert. Concretely — this must not raise.
        txn = await _txn(s, hh, acct.id, "-10.00", merchant="Cafe")
        await s.refresh(txn)

        assert txn.category_id == live.id  # the healthy rule ran on create
        assert txn.field_sources.get("category") == "rule"
        assert txn.is_hidden is False  # the skipped rule's other action did not run
        # And the rule is still there to be fixed: skipping is not deleting, so the
        # household can still see it and switch it off through the API.
        assert [r.name for r in await rules.list_rules(s)] == ["stale", "live"]


async def test_apply_does_not_reach_across_households(household_factory):
    """RLS plus the explicit household filter: a rule in B cannot touch A's rows
    even when its conditions would match them."""
    a = await household_factory(name="A")
    b = await household_factory(name="B")
    async with scoped_session(household_id=a) as s:
        a_acct = await _account(s, a)
        a_txn = await _txn(s, a, a_acct.id, "-10.00", merchant="WHOLE FOODS")
    async with scoped_session(household_id=b) as s:
        b_acct = await _account(s, b)
        b_txn = await _txn(s, b, b_acct.id, "-20.00", merchant="WHOLE FOODS")
        b_cat = await _category(s, b, "Groceries")
        await _rule(s, b, actions={"set_category_id": b_cat.id})

        result = await rules.apply(s, b)
        await s.refresh(b_txn)
        assert result.matched == 1 and result.updated == 1
        assert b_txn.category_id == b_cat.id
    async with scoped_session(household_id=a) as s:
        untouched = await txns.get_transaction(s, a_txn.id)
        assert untouched.category_id is None
        assert "category" not in (untouched.field_sources or {})


# ---- The routes over HTTP --------------------------------------------------

CSRF = "X-CSRF-Token"
_UNSAFE = {"POST", "PATCH", "PUT", "DELETE"}


class _Api:
    """A signed-in client, in miniature.

    ``test_api_owners`` has the full one; this file needs a handful of requests
    through it, and importing across test modules would couple this file to that
    one's internals for no gain.
    """

    def __init__(self, app):
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self._cookie: str | None = None
        self.csrf: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def login(self, email, password="password123"):
        resp = await self._http.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        self._cookie = resp.cookies[SESSION_COOKIE]
        self.csrf = resp.json()["csrf_token"]
        return resp.json()

    async def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if self.csrf and method in _UNSAFE:
            headers[CSRF] = self.csrf
        if self._cookie:
            self._http.cookies.set(SESSION_COOKIE, self._cookie)
        return await self._http.request(method, url, headers=headers, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)


@pytest.fixture
async def api():
    """A signed-in owner client, with a household created for this test alone.

    Built through ``bootstrap_household`` and logged in over HTTP rather than
    signing up: with open signup a second signup *joins whatever household already
    exists*, so on a database shared with the rest of the suite the role this test
    gets back would depend on what ran before it. Creating the household directly
    leaves every other test's data alone.
    """
    from app.db import unscoped_session
    from app.main import create_app
    from app.services import auth as auth_svc

    email = f"{uuid.uuid4().hex[:8]}@example.com"
    async with unscoped_session() as s:
        household, user = await auth_svc.bootstrap_household(
            s, name="Rules over HTTP", base_currency="USD", owner_email=email,
            owner_name="Alex", owner_password="password123",
        )
    async with _Api(create_app()) as client:
        await client.login(email)
        yield client, household.id, user.id


async def _set_role(household_id, user_id, role):
    """Change this user's role in the household.

    The role gate reads the membership on every request (``deps.get_context``), so
    writing the row is what the next request sees — no second account needed to
    reach the member branch.
    """
    from app.db import unscoped_session
    from app.models import HouseholdMember

    async with unscoped_session() as s:
        membership = await s.get(HouseholdMember, (household_id, user_id))
        membership.role = role


_RULE_BODY = {
    "name": "Amazon is shopping",
    "conditions": {"merchant_contains": "amzn"},
    "actions": {"set_hidden": True},
}


async def test_reading_rules_is_open_to_members_but_writing_is_owner_only(api):
    client, household_id, user_id = api
    assert (await client.get("/rules")).json() == []

    created = await client.post("/rules", json=_RULE_BODY)
    assert created.status_code == 201, created.text
    rule = created.json()
    # The response carries the whole closed key set, unset keys as null — that is
    # the shape a client builds a form from; only the keys actually set reach the
    # JSONB, which the round-trip test above checks.
    assert set(rule["conditions"]) == set(CONDITION_KEYS)
    assert rule["conditions"]["merchant_contains"] == "amzn"
    assert rule["conditions"]["amount_max"] is None
    assert rule["actions"]["set_hidden"] is True
    assert rule["priority"] == 100 and rule["enabled"] is True

    # A rule rewrites ledger history — every future ingest through the on-create
    # hook and every existing row through apply — so writing one is the household's
    # decision to make, like deleting an owner (api/rules.py).
    await _set_role(household_id, user_id, "member")
    assert (await client.get("/rules")).status_code == 200  # reading is still fine
    assert (await client.post("/rules", json=_RULE_BODY)).status_code == 403
    assert (await client.patch(f"/rules/{rule['id']}", json={"enabled": False})).status_code == 403
    assert (await client.delete(f"/rules/{rule['id']}")).status_code == 403
    assert (await client.post("/rules/apply")).status_code == 403
    assert (await client.get("/rules")).json()[0]["enabled"] is True  # nothing slipped through

    await _set_role(household_id, user_id, "owner")
    patched = await client.patch(f"/rules/{rule['id']}", json={"priority": 5, "enabled": False})
    assert (patched.status_code, patched.json()["priority"], patched.json()["enabled"]) == (
        200, 5, False,
    )
    assert (await client.delete(f"/rules/{rule['id']}")).status_code == 204
    assert (await client.get("/rules")).json() == []


async def test_a_rule_that_cannot_compile_is_rejected_before_it_is_stored(api):
    client, _, _ = api
    resp = await client.post(
        "/rules", json={"name": "Bad", "conditions": {"description_regex": "(unclosed"}}
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/rules", json={"name": "Bad", "conditions": {"merchant_contain": "x"}}
    )
    assert resp.status_code == 422  # a typo is refused, not silently matched-all
    assert (await client.get("/rules")).json() == []


async def test_apply_over_http_reports_counts_and_is_idempotent(api):
    client, _, _ = api
    acct = (
        await client.post("/accounts", json={"name": "Card", "type": "credit", "currency": "USD"})
    ).json()
    group = (
        await client.post("/category-groups", json={"name": "Expense", "type": "expense"})
    ).json()
    cat = (
        await client.post("/categories", json={"group_id": group["id"], "name": "Groceries"})
    ).json()
    for i in range(2):
        resp = await client.post("/transactions", json={
            "account_id": acct["id"], "amount": f"-{10 + i}.00",
            "transacted_at": "2026-01-05T00:00:00Z", "merchant": "WHOLE FOODS",
        })
        assert resp.status_code == 201, resp.text

    # `/rules/apply` is not read as a rule id: it reaches the apply handler.
    created = await client.post("/rules", json={
        "name": "Groceries", "conditions": {"merchant_contains": "whole foods"},
        "actions": {"set_category_id": cat["id"]},
    })
    assert created.status_code == 201, created.text

    first = await client.post("/rules/apply")
    assert first.status_code == 200, first.text
    assert first.json() == {"matched": 2, "updated": 2}

    second = await client.post("/rules/apply")
    assert second.json() == {"matched": 2, "updated": 0}  # PLAN.md's R bar, over HTTP

    # And the rule's work is visible in the ledger the client reads.
    page = (await client.get("/transactions")).json()
    assert {t["category_id"] for t in page["items"]} == {cat["id"]}
