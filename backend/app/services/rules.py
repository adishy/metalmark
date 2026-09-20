"""Rules engine (WS-R): CRUD, the evaluator, and the two apply paths.

Two things make this more than a filter-and-write loop:

**Provenance decides who may write (ADR-0007/0019).** A rule may fill a blank or
overwrite a ``provider`` value or an earlier rule's, and may never touch a field
a human set — ``field_sources[<field>] == "user"`` is a hard stop. Each field a
rule does write is marked ``"rule"``, so a better rule later can improve it and a
human still outranks both. This is the whole point of the vertical: without it,
re-running rules silently eats human corrections.

**Applying twice must change nothing the second time.** PLAN.md's acceptance bar
for R. That falls out of the same rule: a field already at the value a rule would
write is not a change, so it is not rewritten and not counted in ``updated``.

**A rule can outlive what it points at.** A rule names its category, owner and
tags inside its JSONB, so nothing object-level stops those being deleted — no
foreign key, and ``delete_category`` has no reason to know about rules. That
state is loud where the household asked for rules to run (``apply``), and is
skipped where the household asked for something else (``apply_to_transaction``,
whose caller is inserting a transaction): a stale rule must not turn every future
create into a 404 that names no rule. See ``_missing``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Category, Owner, Tag, Transaction, TransactionTag
from app.models.ledger import Rule
from app.schemas.patch import is_set
from app.schemas.rules import (
    RuleActions,
    RuleApplyResult,
    RuleConditions,
    RuleCreate,
    RuleUpdate,
)
from app.services.errors import LedgerError
from app.services.ownership import require_owners

log = get_logger(__name__)

USER = "user"
RULE = "rule"

#: Rows per pass of the "apply to existing" walk. Small enough that a household
#: with 50k transactions never holds more than a page of ORM objects (each with
#: its splits loaded), large enough that the round trips do not dominate.
APPLY_CHUNK = 500

#: Lower priority runs first. Ties break on ``created_at`` then ``id``, and the
#: ``id`` term is the one that carries the determinism: Postgres ``now()`` is the
#: *transaction* timestamp, so two rules written in one transaction share an
#: ``created_at`` exactly and ``created_at`` alone would leave their order up to
#: the query planner. The id is arbitrary (UUIDv4) but total and — crucially —
#: stable once written, so the same rule set always produces the same result.
#: Rules created through the API are one per request, so in practice they differ
#: on ``created_at`` and run oldest-first within a priority.
_RULE_ORDER = (Rule.priority, Rule.created_at, Rule.id)


# ---- CRUD -----------------------------------------------------------------


async def list_rules(session: AsyncSession) -> list[Rule]:
    return list(
        (await session.execute(select(Rule).order_by(*_RULE_ORDER))).scalars().all()
    )


async def get_rule(session: AsyncSession, rule_id: uuid.UUID) -> Rule:
    """Fetch one rule. RLS scopes the lookup, so a foreign id is a 404."""
    rule = (await session.execute(select(Rule).where(Rule.id == rule_id))).scalar_one_or_none()
    if rule is None:
        raise LedgerError("Rule not found", 404)
    return rule


async def create_rule(
    session: AsyncSession, household_id: uuid.UUID, data: RuleCreate
) -> Rule:
    conditions, actions = data.conditions, data.actions
    # No rule row yet, so no id to name it by: refs are checked on the actions
    # alone, which is the one caller where that is the whole story.
    await _require_actions(session, [actions])
    rule = Rule(
        household_id=household_id,
        name=data.name,
        priority=data.priority,
        enabled=data.enabled,
        conditions=conditions.stored(),
        actions=actions.stored(),
    )
    session.add(rule)
    await session.flush()
    return rule


async def update_rule(
    session: AsyncSession, rule_id: uuid.UUID, data: RuleUpdate
) -> Rule:
    """Patch a rule. Absent means "no change"; explicit null clears conditions or
    actions (the other fields refuse null — see ``RuleUpdate``)."""
    rule = await get_rule(session, rule_id)
    if is_set(data, "name"):
        rule.name = data.name
    if is_set(data, "priority"):
        rule.priority = data.priority
    if is_set(data, "enabled"):
        rule.enabled = data.enabled
    if is_set(data, "conditions") or is_set(data, "actions"):
        # Only a patch that can introduce a reference is validated, and then the
        # whole rule is: renaming, re-prioritizing or — above all — *disabling* a
        # rule whose category has since been deleted has to keep working, or a
        # broken rule could never be switched off.
        #
        # Validated *before* assigning: assigning first and raising after would
        # leave the refused values on the ORM object, and the next flush in this
        # session (the next successful patch, or the request's own commit) would
        # then persist exactly what the 404 refused.
        await _require_actions(session, [_actions_after(rule, data)])
    # Wholesale replacement, not a key-wise merge: the builder edits a rule as one
    # object, and a merge would make "I deleted that condition" unsendable.
    if is_set(data, "conditions"):
        rule.conditions = data.conditions.stored() if data.conditions is not None else {}
    if is_set(data, "actions"):
        rule.actions = data.actions.stored() if data.actions is not None else {}
    await session.flush()
    return rule


def _actions_after(rule: Rule, data: RuleUpdate) -> RuleActions:
    """The actions the rule would hold once this patch lands.

    A patch replaces them wholesale, so a null means "empty" and an absent key
    means "the stored ones stand" — the same distinction PATCH makes everywhere.
    """
    if is_set(data, "actions"):
        return data.actions if data.actions is not None else RuleActions()
    return RuleActions.model_validate(rule.actions or {})


async def delete_rule(session: AsyncSession, rule_id: uuid.UUID) -> None:
    rule = await get_rule(session, rule_id)
    await session.delete(rule)
    await session.flush()


# ---- The evaluator --------------------------------------------------------


@dataclass(frozen=True)
class _CompiledRule:
    """A stored rule, parsed. The id rides along so a rule dropped for a stale
    reference can be named in the log rather than silently disappearing."""

    id: uuid.UUID
    conditions: RuleConditions
    actions: RuleActions


def _compiled(rule: Rule) -> _CompiledRule:
    """Parse a stored rule once per run, not once per row.

    A stored row that fails to validate is allowed to raise: the API is the only
    writer and it validates on the way in, so a bad rule means the table was
    written directly — and skipping it quietly would hide exactly the mistake
    worth knowing about.
    """
    return _CompiledRule(
        id=rule.id,
        conditions=RuleConditions.model_validate(rule.conditions or {}),
        actions=RuleActions.model_validate(rule.actions or {}),
    )


def _contains(haystack: str | None, needle: str) -> bool:
    """Case-insensitive substring. A NULL column contains nothing, rather than
    matching everything "contains" over an empty string would."""
    return haystack is not None and needle.lower() in haystack.lower()


def _searches(text: str | None, pattern: str) -> bool:
    """``re.search``, with the same NULL rule: no description, no match."""
    return text is not None and re.search(pattern, text) is not None


def _direction_ok(direction: str, amount) -> bool:
    # Zero is neither money in nor money out, so it matches neither.
    return amount > 0 if direction == "in" else amount < 0


def _matches(conditions: RuleConditions, txn: Transaction) -> bool:
    """Every set condition must hold (AND); an unset one constrains nothing.

    Written as the conjunction it is, so a reader can see the whole rule at once
    and adding a key is one line. Bounds are on the SIGNED amount — expenses are
    negative, so "spending over $100" is ``amount_max = -100``, not ``amount_min``.
    """
    return all(
        (
            conditions.merchant_contains is None
            or _contains(txn.merchant, conditions.merchant_contains),
            conditions.description_regex is None
            or _searches(txn.description, conditions.description_regex),
            conditions.amount_min is None or txn.amount >= conditions.amount_min,
            conditions.amount_max is None or txn.amount <= conditions.amount_max,
            conditions.direction is None
            or _direction_ok(conditions.direction, txn.amount),
            conditions.account_ids is None or txn.account_id in conditions.account_ids,
            conditions.category_id is None or txn.category_id == conditions.category_id,
            conditions.is_pending is None or txn.is_pending == conditions.is_pending,
        )
    )


def _write(
    txn: Transaction, changed: set[str], *, field: str, attr: str, value: object
) -> None:
    """Set one field, if provenance allows, and record it as rule-set.

    ``field`` is the ``field_sources`` key (``category``), ``attr`` the column
    (``category_id``): ADR-0007 keys provenance by field, and the manual writers
    already follow that (`transactions.py` marks ``"owner"`` while assigning
    ``owner_id``), so the two writers agree on the vocabulary.
    """
    sources = txn.field_sources or {}
    if sources.get(field) == USER:
        # A human's decision outranks every rule and the provider (ADR-0007).
        return
    if getattr(txn, attr) == value:
        # Already at the target value — nothing to write, nothing changed. This is
        # what makes a second apply a no-op, and it is also why a rule does not
        # "claim" a provider value it happens to agree with: nobody set it.
        return
    setattr(txn, attr, value)
    sources = dict(sources)
    sources[field] = RULE
    txn.field_sources = sources
    changed.add(field)


def _add_tags(
    session: AsyncSession,
    txn: Transaction,
    wanted: Iterable[uuid.UUID],
    present: set[uuid.UUID],
    changed: set[str],
) -> None:
    """Union ``wanted`` into the row's tags (never removes one).

    ``present`` is both the row's existing tags and the accumulator for this run,
    so two rules adding the same tag in one pass cannot collide on the
    ``transaction_tags`` primary key at flush time.
    """
    added = False
    for tag_id in wanted:
        if tag_id in present:
            continue
        present.add(tag_id)
        session.add(TransactionTag(transaction_id=txn.id, tag_id=tag_id))
        added = True
    if added:
        changed.add("tags")


def _apply_to_row(
    session: AsyncSession,
    txn: Transaction,
    rules: list[_CompiledRule],
    present_tags: set[uuid.UUID],
) -> tuple[bool, set[str]]:
    """Run every rule over one transaction, in order.

    Returns ``(matched, changed)``: whether any rule's conditions hit, and which
    provenance fields actually ended up written. A later rule can overwrite an
    earlier rule's value (ADR-0007 lets a rule beat an earlier rule), which is
    what makes priority mean something.
    """
    matched = False
    changed: set[str] = set()
    for rule in rules:
        conditions, actions = rule.conditions, rule.actions
        if not _matches(conditions, txn):
            continue
        matched = True
        if actions.set_category_id is not None:
            _write(txn, changed, field="category", attr="category_id",
                   value=actions.set_category_id)
        if actions.set_owner_id is not None:
            _write(txn, changed, field="owner", attr="owner_id",
                   value=actions.set_owner_id)
        if actions.rename_merchant is not None:
            _write(txn, changed, field="merchant", attr="merchant",
                   value=actions.rename_merchant)
        if actions.set_hidden is not None:
            _write(txn, changed, field="is_hidden", attr="is_hidden",
                   value=actions.set_hidden)
        if actions.mark_reviewed is not None:
            _write(txn, changed, field="review_status", attr="review_status",
                   value="reviewed" if actions.mark_reviewed else "needs_review")
        if actions.add_tag_ids is not None:
            _add_tags(session, txn, actions.add_tag_ids, present_tags, changed)
    return matched, changed


# ---- Referenced ids -------------------------------------------------------


#: What an action's reference is, when it does not resolve — the wording the rest
#: of the ledger uses for the same missing row, plus the fact worth knowing here.
_REF_MESSAGES = {
    "owner": "Owner not found",
    "category": "Category not found",
    "tag": "Tag not found",
}


async def _known(
    session: AsyncSession, column, wanted: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``wanted`` this session can see. RLS does the scoping, so another
    household's id is simply absent — the same answer as one that never existed."""
    if not wanted:
        return set()
    return set(
        (await session.execute(select(column).where(column.in_(wanted)))).scalars().all()
    )


async def _missing_kinds(
    session: AsyncSession, actions_list: list[RuleActions]
) -> dict[int, str]:
    """Index into ``actions_list`` -> the kind of reference it names that is gone.

    Reported by index rather than by rule id because one caller has no rule yet:
    ``create_rule`` checks the actions it is about to store.

    The foreign keys would not catch this on their own: a constraint is satisfied
    by a row that exists anywhere, so a rule naming another household's category
    or tag would write straight through it — RLS governs the rows *this* session
    may see, not the rows a constraint may point at. Looking each id up through
    the RLS-scoped session makes both cases the same 404 an ordinary foreign id
    gets. Owners are checked here too so this is a complete answer, while
    ``require_owners`` stays the definition of an owner the household may use.

    Conditions are deliberately *not* checked. A condition can only narrow what
    matches, so one naming a deleted account or a foreign id simply never fires —
    the right answer for "charges in that account" once the account is gone, and
    it keeps a stale condition from blocking anything at all.
    """
    known_owners = await _known(
        session, Owner.id, {a.set_owner_id for a in actions_list} - {None}
    )
    known_categories = await _known(
        session, Category.id, {a.set_category_id for a in actions_list} - {None}
    )
    wanted_tags = {t for a in actions_list if a.add_tag_ids for t in a.add_tag_ids}
    known_tags = await _known(session, Tag.id, wanted_tags)

    out: dict[int, str] = {}
    for i, actions in enumerate(actions_list):
        if actions.set_owner_id is not None and actions.set_owner_id not in known_owners:
            out[i] = "owner"
        elif (
            actions.set_category_id is not None
            and actions.set_category_id not in known_categories
        ):
            out[i] = "category"
        elif actions.add_tag_ids and not set(actions.add_tag_ids) <= known_tags:
            out[i] = "tag"
    return out


async def _require_actions(
    session: AsyncSession, actions_list: list[RuleActions]
) -> None:
    """Refuse actions naming something this household cannot resolve.

    The loud half of the policy; ``load_rules`` is the quiet one. These callers
    are the household naming a reference itself — creating or editing a rule,
    or asking for the rules to run — so a bad reference is a 404 it can act on,
    rather than an enabled rule that silently does nothing.
    """
    await require_owners(session, [a.set_owner_id for a in actions_list])
    missing = await _missing_kinds(session, actions_list)
    if missing:
        kind = _REF_MESSAGES[next(iter(missing.values()))]
        raise LedgerError(f"{kind} — an enabled rule's action refers to it", 404)


async def _require_refs(session: AsyncSession, rules: list[_CompiledRule]) -> None:
    await _require_actions(session, [r.actions for r in rules])


async def _enabled_rules(session: AsyncSession) -> list[_CompiledRule]:
    rows = (
        await session.execute(select(Rule).where(Rule.enabled.is_(True)).order_by(*_RULE_ORDER))
    ).scalars().all()
    return [_compiled(r) for r in rows]


@dataclass(frozen=True)
class LoadedRules:
    """Enabled rules, compiled once, for a caller that has many rows to run them
    over — ``apply_to_transaction(..., loaded=...)`` rather than paying for the
    rules and their reference lookups on every row."""

    compiled: list[_CompiledRule]
    #: Rules left out because an action names a deleted owner, category or tag.
    skipped_ids: list[uuid.UUID]


async def load_rules(session: AsyncSession) -> LoadedRules:
    """The enabled rules, minus the ones whose actions cannot be honored.

    The on-create path runs rules as a side effect of something else the caller
    asked for, and one stale rule must not fail that: a rule named a category
    that was later deleted, so *every* future create raises a 404 that has nothing
    to do with the create and names no rule. The household is wedged, and the fix
    (delete the rule) is not reachable through the broken endpoint. So the rule is
    skipped here and named in the log, and ``apply`` stays the place where a
    household gets told its rule set is broken — there it asked for exactly that.
    """
    found = await _enabled_rules(session)
    broken = await _missing_kinds(session, [r.actions for r in found])
    if broken:
        log.warning(
            "rules.skipped_stale_actions",
            rule_ids=sorted(str(found[i].id) for i in broken),
            refs=sorted(set(broken.values())),
        )
    return LoadedRules(
        compiled=[r for i, r in enumerate(found) if i not in broken],
        skipped_ids=[found[i].id for i in broken],
    )


async def _tag_map(
    session: AsyncSession, txn_ids: list[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Existing tags for a batch of transactions, in one query.

    One query per chunk rather than one per row: "apply to existing" with a
    tagging rule is otherwise an N+1 over the whole ledger.
    """
    if not txn_ids:
        return {}
    rows = (
        await session.execute(
            select(TransactionTag.transaction_id, TransactionTag.tag_id).where(
                TransactionTag.transaction_id.in_(txn_ids)
            )
        )
    ).all()
    out: dict[uuid.UUID, set[uuid.UUID]] = {}
    for txn_id, tag_id in rows:
        out.setdefault(txn_id, set()).add(tag_id)
    return out


# ---- Applying -------------------------------------------------------------


async def apply_to_transaction(
    session: AsyncSession,
    household_id: uuid.UUID,
    txn: Transaction,
    *,
    loaded: LoadedRules | None = None,
) -> set[str]:
    """Run the enabled rules over **one** transaction — the on-create entry point.

    Returns the ``field_sources`` keys it changed (``{"category", "tags"}``, not
    column names), which is what a caller wants to log or count; an empty set
    means the row was already where the rules would put it, or a human got there
    first.

    Rules whose actions name something deleted are skipped rather than raised
    (``load_rules``): the caller asked to insert a transaction, and that must not
    fail because of a rule it never mentioned.

    ``loaded`` lets a caller looping over rows — a CSV import — fetch and compile
    the rules once instead of per row; with no tagging rules in play, a call that
    passes it runs no queries of its own. Omit it and the rules are loaded here.

    Neither commits nor flushes *the caller's* row: the caller owns the
    transaction, so a sync ingest that runs this after inserting a provider row
    commits the row and its rule-applied fields as one unit. It does flush when it
    changed something, because the writes have to be visible — a caller reading the
    row back, or running the rules over it a second time, must see what the first
    pass did, and this app's sessions do not autoflush (``db.scoped_session`` uses
    ``session.begin()``, whose transaction disables it). A flush is not a commit.

    Two callers, both of which rely on that: ``services/transactions.py``'s
    create/update path (a human's row is rule-matched as it is written) and
    ``services/sync.py``'s ingest (a provider's). They pass their own
    ``loaded=`` so the rule set is compiled once per run rather than once per row.
    """
    if txn.household_id != household_id:
        # Belt and braces, and it makes the argument load-bearing rather than
        # decorative: RLS would turn a write to a foreign row into a silent
        # zero-row UPDATE, and a caller that mixed its arguments up deserves to
        # hear about it.
        raise LedgerError("Transaction does not belong to this household", 404)

    if loaded is None:
        loaded = await load_rules(session)
    if not loaded.compiled:
        return set()
    present = set()
    if any(r.actions.add_tag_ids for r in loaded.compiled):
        # Only a tagging rule needs the row's tags, and the lookup is the one
        # query left per row — so a rule set that never tags does not pay it.
        present = (await _tag_map(session, [txn.id])).get(txn.id, set())
    _matched, changed = _apply_to_row(session, txn, loaded.compiled, present)
    if changed:
        # Nothing to publish when nothing changed, and on the second pass over a
        # row that is the common case — so a repeat costs no round trip.
        await session.flush()
    return changed


async def apply(session: AsyncSession, household_id: uuid.UUID) -> RuleApplyResult:
    """Run the enabled rules over every existing transaction ("apply to existing").

    Idempotent, and that is the acceptance bar (PLAN.md R): a second run over the
    same rule set reports the same ``matched`` and ``updated == 0``, because every
    field is already at the value the rule would write.

    Walks the ledger in id-ordered chunks rather than loading it: a household with
    50k transactions must not need 50k rows in memory to categorize them. No rule
    touches ``id``, so the keyset cursor cannot skip a row.

    Unlike the on-create path this one *is* strict about the rule set
    (``_require_refs``): the household asked to apply these rules, so a rule whose
    action cannot be honored is a 404 worth showing, not a rule to skip.
    """
    compiled = await _enabled_rules(session)
    if not compiled:
        return RuleApplyResult(matched=0, updated=0)
    await _require_refs(session, compiled)
    tags_wanted = any(r.actions.add_tag_ids for r in compiled)

    matched = 0
    updated = 0
    last_id: uuid.UUID | None = None
    while True:
        # Explicit household_id on top of the RLS policy: this is the one code
        # path that deliberately writes across every row the household has, so the
        # cheap belt costs nothing and reads as what it is.
        stmt = (
            select(Transaction)
            .where(Transaction.household_id == household_id)
            .order_by(Transaction.id)
            .limit(APPLY_CHUNK)
        )
        if last_id is not None:
            stmt = stmt.where(Transaction.id > last_id)
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            break
        last_id = rows[-1].id

        tags = await _tag_map(session, [r.id for r in rows]) if tags_wanted else {}
        for txn in rows:
            did_match, changed = _apply_to_row(
                session, txn, compiled, tags.setdefault(txn.id, set())
            )
            if did_match:
                matched += 1
            if changed:
                updated += 1
        # Flush per chunk so the session does not accumulate a whole ledger's
        # worth of dirty state (and so the next chunk's tag lookup sees this one's
        # inserts).
        await session.flush()

    return RuleApplyResult(matched=matched, updated=updated)
