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

    @field_validator("add_tag_ids")
    @classmethod
    def _no_empty_list(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if not value:
            raise ValueError("add_tag_ids cannot be empty")
        return value


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
