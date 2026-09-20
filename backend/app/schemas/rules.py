"""Rule shapes, and the closed key sets for ``conditions`` / ``actions``.

A rule is stored as two JSONB blobs (ARCHITECTURE §2), so nothing in the database
would object to a misspelled key — ``{"merchant_contain": "AMZN"}`` would store
cleanly and then match everything, forever, with no error anywhere. The key sets
are therefore closed here: ``extra="forbid"`` turns an unknown key into a 422 at
write time, which is the only moment a human is around to see it.

Amounts are ``Decimal`` on the wire like every other money field (ADR-0005).
They are *stored* as decimal strings inside the JSONB — see ``_stored``.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.patch import is_set

# The closed sets, exported for the tests and for anyone reading the two models
# below wondering what the *other* half of the contract is.
CONDITION_KEYS = frozenset(
    {
        "merchant_contains",
        "description_regex",
        "amount_min",
        "amount_max",
        "direction",
        "account_ids",
        "category_id",
        "is_pending",
    }
)
ACTION_KEYS = frozenset(
    {
        "set_category_id",
        "add_tag_ids",
        "set_owner_id",
        "rename_merchant",
        "set_hidden",
        "mark_reviewed",
        "split",
    }
)


class RuleBlob(BaseModel):
    """Base for the two stored halves of a rule: closed keys, JSON-safe dumps."""

    model_config = ConfigDict(extra="forbid")

    def stored(self) -> dict:
        """Dump for JSONB storage.

        ``mode="json"`` and not the default: a ``Decimal`` reaching the JSONB
        encoder is a ``TypeError`` at flush time, and the alternatives are worse —
        a float loses cents (ADR-0005) and ``default=str`` would quietly stringify
        anything else that slipped in. So money lands as a decimal string, and
        ``parse_amount`` reads it back."""
        return self.model_dump(mode="json", exclude_none=True)


class RuleConditions(RuleBlob):
    """All optional, all AND-ed. An unset key constrains nothing."""

    #: Case-insensitive substring of ``transactions.merchant``. A NULL merchant
    #: matches nothing — "contains" against a value that is not there is false,
    #: not vacuously true.
    merchant_contains: str | None = Field(default=None, min_length=1, max_length=200)
    #: Python ``re.search`` against ``transactions.description`` (same NULL rule).
    #: Compiled here so a pattern that cannot compile is a 422 rather than an
    #: exception in the middle of a batch apply.
    description_regex: str | None = Field(default=None, min_length=1, max_length=500)
    #: Bounds on the SIGNED ``amount`` — expenses are negative, so "spending over
    #: $100" is ``amount_max: -100``, not ``amount_min: 100``.
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    #: ``in`` = amount > 0, ``out`` = amount < 0. Zero matches neither.
    direction: Literal["in", "out"] | None = None
    account_ids: list[uuid.UUID] | None = None
    category_id: uuid.UUID | None = None
    is_pending: bool | None = None

    @field_validator("description_regex")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value

    @field_validator("account_ids")
    @classmethod
    def _no_empty_list(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        # An empty list matches nothing, so a rule carrying one is inert while
        # looking alive. Say so instead of quietly never firing.
        if not value:
            raise ValueError("account_ids cannot be empty")
        return value

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> RuleConditions:
        # Inverted bounds match nothing, so they are a rule that looks active and
        # can never fire — the same silent no-op the closed keys exist to prevent.
        if (
            self.amount_min is not None
            and self.amount_max is not None
            and self.amount_min > self.amount_max
        ):
            raise ValueError("amount_min cannot be greater than amount_max")
        return self


class RuleSplitLeg(RuleBlob):
    """One leg of a rule's ``split`` action (ADR-0031 §1-2).

    A leg names a **share of the parent**, never a sum of its own: exactly one leg
    in a split is the ``remainder``, and it takes whatever the parent has left. A
    leg that is not the remainder carries exactly one of ``amount`` or
    ``percent``, and the schema is a 422 otherwise.

    The split is balanced **by construction** rather than validated: a rule is
    written once and applied to transactions whose amounts it cannot know, so
    "$50 here and the rest there" is expressed as an amount leg plus the
    remainder leg — never as two fixed amounts, which a bank correction would turn
    into a split that does not sum to its parent. That expressiveness cost is
    deliberate; the rule editor has to explain it (ADR-0031, Consequences).

    ``amount`` is signed like every money field (ADR-0005) and its sign must match
    the parent's — a mixed-sign split is a transfer, which ADR-0018 models
    properly and this does not. ``percent`` is ``0 < p < 100`` and takes that
    fraction of the parent's *magnitude*, signed to the parent: a rule splitting a
    -$100 expense 25/75 writes two negative legs, never a positive one.
    """

    amount: Decimal | None = None
    percent: Decimal | None = None
    #: ``true`` marks this leg as the remainder. It takes no amount or percent of
    #: its own — it takes what is left, which is what makes the sum exact for any
    #: parent amount.
    remainder: bool | None = None
    category_id: uuid.UUID | None = None
    #: Null = inherit the parent transaction's owner (ADR-0026), exactly as
    #: ``SplitIn`` does on the manual path.
    owner_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)

    # The checks that make a leg a leg are on ``RuleActions`` rather than here,
    # because every one of them has to name *which* leg it is about and a leg does
    # not know its own index. They still run on every path: a leg reaches storage
    # only inside an action, and the action is validated on the way in and read
    # back through ``_compiled``.



class RuleActions(RuleBlob):
    """What a matching rule writes. Provenance still has the final say (ADR-0007):
    the engine skips any field a human has already set on the row."""

    set_category_id: uuid.UUID | None = None
    #: Additive (union) — never removes a tag. Re-running adds nothing.
    add_tag_ids: list[uuid.UUID] | None = None
    set_owner_id: uuid.UUID | None = None
    rename_merchant: str | None = Field(default=None, min_length=1, max_length=300)
    #: ``true`` hides the row, ``false`` unhides it. Both directions are real
    #: rules, so there is no "presence means true" shortcut.
    set_hidden: bool | None = None
    #: ``true`` -> review_status "reviewed", ``false`` -> "needs_review".
    mark_reviewed: bool | None = None
    #: The legs of a rule-made split (ADR-0031). Applied only to a transaction
    #: whose splits the rule owns — a human's split is final (§3, ADR-0007).
    split: list[RuleSplitLeg] | None = None

    @field_validator("add_tag_ids")
    @classmethod
    def _no_empty_list(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if not value:
            raise ValueError("add_tag_ids cannot be empty")
        return value

    @model_validator(mode="after")
    def _split_is_balanced(self) -> RuleActions:
        legs = self.split
        if legs is None:
            return self
        # Per leg, first, and each message names the leg: a split has several of
        # them and "the schema is a 422 otherwise" is only actionable if the 422
        # says which leg to fix and why (ADR-0031 §1-2).
        for i, leg in enumerate(legs, start=1):
            if leg.remainder is True:
                if leg.amount is not None or leg.percent is not None:
                    raise ValueError(
                        f"split leg {i}: a remainder leg takes no amount or "
                        f"percent — it takes what is left after the other legs, "
                        f"which is what keeps the split balanced for any amount"
                    )
                continue
            if leg.remainder is False:
                raise ValueError(
                    f"split leg {i}: remainder is either true or omitted; "
                    f"``false`` would read as a leg that takes the rest and does "
                    f"not"
                )
            if leg.amount is None and leg.percent is None:
                raise ValueError(
                    f"split leg {i}: every leg that is not the remainder carries "
                    f"exactly one of amount or percent, and this one carries "
                    f"neither"
                )
            if leg.amount is not None and leg.percent is not None:
                raise ValueError(
                    f"split leg {i}: carries both amount and percent; a leg takes "
                    f"one share or the other, never two"
                )
            if leg.percent is not None and not (0 < leg.percent < 100):
                raise ValueError(
                    f"split leg {i}: percent must be greater than 0 and less than "
                    f"100 (got {leg.percent}) — the leg that takes the rest is the "
                    f"remainder leg"
                )
            if leg.amount is not None and leg.amount == 0:
                raise ValueError(
                    f"split leg {i}: an amount leg must be non-zero — a leg that "
                    f"moves nothing is a leg to delete, not one to carry"
                )
        if len(legs) < 2:
            # One leg is the whole transaction wearing a split's clothes, and zero
            # is nothing at all. Both are inert while looking alive — the same
            # silent no-op the closed key set exists to prevent.
            raise ValueError(
                "a split needs at least two legs (an amount or percent leg plus "
                "the remainder leg); a one-leg split is just the transaction"
            )
        remainders = [i for i, leg in enumerate(legs, start=1) if leg.remainder is True]
        if not remainders:
            raise ValueError(
                "a split needs exactly one leg with remainder: true, and this one "
                "has none — without it the legs cannot sum to a transaction amount "
                "the rule does not know when it is written"
            )
        if len(remainders) > 1:
            named = " and ".join(f"leg {i}" for i in remainders)
            raise ValueError(
                f"{named} are both marked remainder: true — exactly one leg takes "
                f"what is left, because two would have no principle to divide it by"
            )
        # Every amount leg must share the parent's sign (§2), and the schema does
        # not know the parent — but it does know the legs, and they can only all
        # match one sign if they agree with each other. That is what makes a
        # mixed-sign split (a transfer, ADR-0018's job) unresolvable here.
        signs = {
            i for i, leg in enumerate(legs, start=1)
            if leg.amount is not None and leg.amount > 0
        }
        amounts = [i for i, leg in enumerate(legs, start=1) if leg.amount is not None]
        if len(signs) not in (0, len(amounts)):
            raise ValueError(
                f"split legs {', '.join(str(i) for i in amounts)} are amount legs "
                f"and they must all share the parent transaction's sign — legs "
                f"pointing both ways are a transfer, not a split"
            )
        # The remainder takes the parent's amount minus the percent legs' share, so
        # percents summing to 100 or more would leave it with the opposite sign to
        # its own parent. That is checkable without knowing the amount, so it is
        # refused here rather than becoming a split whose remainder contradicts it.
        taken = sum((leg.percent for leg in legs if leg.percent is not None), Decimal(0))
        if taken >= 100:
            raise ValueError(
                f"the percent legs take {taken}% of the transaction between them, "
                f"which leaves the remainder leg nothing (or less than nothing) to "
                f"take — they must come to less than 100%"
            )
        return self


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    #: Lower runs first. Sparse on purpose: a new rule slips between two existing
    #: ones by taking a priority in the gap, with no renumbering.
    priority: int = 100
    enabled: bool = True
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    actions: RuleActions = Field(default_factory=RuleActions)

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value.strip()


class RuleUpdate(BaseModel):
    """Absent means "no change"; explicit null means "clear" for conditions and
    actions (an empty rule that matches everything and writes nothing is a
    legitimate way to park a rule without deleting it).

    ``name``/``priority``/``enabled`` cannot be cleared — a rule with no name or
    no position is a client bug, and saying so beats a NOT NULL violation later.
    ``conditions``/``actions`` replace wholesale rather than merging key by key:
    the builder dialog edits a rule as one object, and a key-wise merge would make
    "I removed that condition" inexpressible.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    priority: int | None = None
    enabled: bool | None = None
    conditions: RuleConditions | None = None
    actions: RuleActions | None = None

    @model_validator(mode="after")
    def _required_fields_not_clearable(self) -> RuleUpdate:
        for field in ("name", "priority", "enabled"):
            if is_set(self, field) and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        # The None guard matters: a field validator runs before the model
        # validator that refuses an explicit null name, so without it a null name
        # would surface as an AttributeError rather than a 422.
        if value is None:
            return value
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value.strip()


class RuleOut(BaseModel):
    """No ``updated_at``, deliberately. The column exists (``TimestampMixin``) but
    is *expired* after an UPDATE flush — it is produced by Postgres, so the ORM
    has to re-read it — and serializing an expired attribute inside an async
    response is a ``MissingGreenlet`` rather than a value. Returning it would mean
    a refresh on every patch for a timestamp nothing renders; no other response in
    the ledger exposes one. The generation of an unset key (``condition``
    instead of ``conditions``) is refused by ``RuleBlob``, so the shape a client
    sees is the closed set with nulls, which is what a form is built from.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    priority: int
    enabled: bool
    conditions: RuleConditions
    actions: RuleActions
    created_at: datetime


class RuleApplyResult(BaseModel):
    """The two counts "apply to existing" reports, and they are not the same
    number (PLAN.md's R acceptance bar).

    Both count **transactions**, not (transaction, rule) pairs, so they are
    directly comparable: ``matched`` is how many rows at least one enabled rule's
    conditions matched, ``updated`` is how many of those the engine actually
    changed. A second run over the same rule set reports the same ``matched`` and
    ``updated == 0`` — every field is already at the value the rule would write,
    and re-writing it is not a change.
    """

    matched: int
    updated: int
