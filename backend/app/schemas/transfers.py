"""Wire models for the manual transfer picker (ADR-0008/0018).

Linking already has its shapes in ``schemas.transactions`` (``TransferLink`` /
``TransferOut``), frozen with the transaction contract. What lives here is the
*read* the picker needs before a link exists: which rows could be the other leg,
and what pairing each one would cost.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.transactions import TransactionOut


class TransferCandidateOut(BaseModel):
    """One possible counterpart leg, with the price of pairing it.

    ``fx_cost_base`` is what the transfer group's ``fx_cost_base`` would become if
    the user committed — Σ base_amount of the two legs. So it is ``None`` for a
    clean same-currency pair (the legs cancel exactly) and the real spread for a
    cross-currency one (ADR-0018). It is computed by the same helper that
    ``link_transfer`` stores, so the number shown before linking is the number
    that lands on the group after it.

    ``within_tolerance`` says the pair's base amounts cancel inside the matching
    tolerance. False is not a refusal: ADR-0018 keeps explicit user linking as the
    override, so the row is still offered — this flag plus the residual are how
    the UI tells "the bank's rate differed slightly" apart from "that is probably
    not the other leg at all".
    """

    transaction: TransactionOut
    # Whole days between the legs, floored — the window is symmetric, so this is
    # never negative.
    days_apart: int
    fx_cost_base: Decimal | None
    within_tolerance: bool


class TransferCandidatesOut(BaseModel):
    items: list[TransferCandidateOut]


class TransferDetailOut(BaseModel):
    """A linked transfer read back whole — the group, and both legs with it.

    A transaction knows only its ``transfer_group_id``, so this is the only way a
    detail view can show the *other* leg. ``fx_cost_base`` is part of that read
    rather than something the client derives, because ADR-0018 requires the cost
    of a cross-currency transfer to be shown: leaving it out would make hiding it
    the path of least resistance.
    """

    transfer_group_id: uuid.UUID
    matched_by: str
    fx_cost_base: Decimal | None
    # Ordered by date then id — deterministic, with no claim about which leg is
    # "the" transfer. A caller picks the side it wants by the amount's sign.
    legs: list[TransactionOut]
