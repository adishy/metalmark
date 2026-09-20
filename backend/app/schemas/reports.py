from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# The granularity vocabulary has exactly one definition, in the pure module that
# implements it — a second Literal here would be a second place for `quarter` to
# go missing. `periods` imports nothing but the standard library.
from app.services.periods import Granularity


class NetWorthPoint(BaseModel):
    date: date
    net_worth: Decimal


class CashFlowPoint(BaseModel):
    """One bucket of the series.

    ``date`` is the bucket's first day **within the window**, not the period it
    belongs to: a window opening on 15 March labels its first monthly bar
    `2026-03-15`, which is a day the report actually covers. Naming the unclipped
    period (`2026-03-01`) would put a date in the payload that lies outside the
    window the same payload echoes — exactly the confusion this field replaced.
    """

    date: date
    income: Decimal
    expense: Decimal
    net: Decimal


class CashFlowSeries(BaseModel):
    base_currency: str
    # The window this answered for, echoed rather than left implicit: a chart then
    # labels its own axis from the data it drew instead of from the controls, so
    # the two cannot disagree about what is on screen. `granularity` is the
    # *resolved* value — never `auto`, which is a request rather than a period,
    # and handing it back would leave the chart doing the resolution it delegated.
    start: date
    end: date
    granularity: Granularity
    points: list[CashFlowPoint]
    # Always "row": an owner filter selects entries, not accounts, so this is the
    # mirror of NetWorthSeries.attribution. The two reports take the same owner_id
    # and answer different questions with it, so the payload says which one it
    # answered rather than leaving a reader to assume they add up (ADR-0026).
    attribution: str = "row"


class CashFlowSankeyRow(BaseModel):
    """One node of the cash-flow graph — where a group of entries came from, or went.

    ``total`` is a **magnitude**: always positive, because the list this row is in
    carries the direction (see ``CashFlowSankey``).
    """

    # The row's *identity*, which is neither its label nor its ``category_id``.
    # Two categories may share a name and a household may name one "Uncategorized",
    # so grouping by what a reader sees would merge unrelated money — a chart
    # builds its node ids from this instead. The rows that are not categories at
    # all (unfiled transactions, investment income, investment fees) get keys of
    # their own so they cannot collide with a real category either.
    key: str
    label: str
    # ``None`` for the rows above that are not a category. Carried so a UI can link
    # a row to its category without parsing ``key``, which is not a wire format.
    category_id: uuid.UUID | None
    total: Decimal


class CashFlowSankey(BaseModel):
    """The window's cash flow as the two sides of one graph.

    ``income`` and ``expense`` are **rows, not nodes and links**: grouping is the
    arithmetic, and the picture's shape follows from it. Sending the shape would
    put a layout decision on the wire where no reader could check it.

    Every ``total`` here — the rows and the two figures below alike — is a
    magnitude, and ``net`` is the only signed number. That is why ``total_expense``
    is positive where ``CashFlowPoint.expense`` is not: a Sankey encodes direction
    by which side a node sits on, and a value negative on both sides is not a graph
    anyone can draw or read.

    ``total_income`` and ``total_expense`` are the sums of the rows above them, so
    the picture balances **by construction** — the left side plus ``net`` equals
    the right side, with no residual to reconcile and no rounding gap to explain.
    """

    base_currency: str
    start: date
    end: date
    income: list[CashFlowSankeyRow]
    expense: list[CashFlowSankeyRow]
    total_income: Decimal
    total_expense: Decimal
    # ``total_income - total_expense``: the one signed figure, and the value the
    # middle of the graph is drawn at.
    net: Decimal
    # Always "row", for the same reason as CashFlowSeries.attribution.
    attribution: str = "row"
    # A flow dropped for want of an FX rate is a missing branch of a picture whose
    # whole claim is that it is complete, so this is not a footnote here.
    warnings: list[str] = []


class CategorySpendRow(BaseModel):
    category_id: uuid.UUID | None
    category_name: str
    total: Decimal


class UnexplainedAccount(BaseModel):
    """One account's share of the residual — the name behind the number.

    A residual with no name is not a finding, so the report says which accounts the
    unexplained change came from rather than printing one figure and leaving the
    reader to guess whether the app is broken. Usually they are accounts whose
    balance is an *observation* the flows we hold do not cover — a first bank sync,
    whose balance is the bank's number from today while the history starts where the
    pull window starts.
    """

    account_id: uuid.UUID
    name: str
    amount: Decimal


class NetWorthSeries(BaseModel):
    """The net-worth series and the reconciliation of its change (ADR-0032).

    ``extra="forbid"`` is load-bearing rather than tidy: the API builds this from
    the service's dict, and Pydantic's default is to *drop* a key it does not know
    about — so a term added to the identity but forgotten here would vanish from the
    response with nothing failing, and the terms that did land would no longer add
    up to the delta. Forbidding extras turns that into a loud error instead.
    """

    model_config = ConfigDict(extra="forbid")

    base_currency: str
    # Echoed for the same reason as CashFlowSeries's: the chart labels itself.
    start: date
    end: date
    # Sets the *points and nothing else*. Every term of the identity below, and
    # every per-account decomposition, is scoped to the whole window — the identity
    # is a claim about the window, so a reader switching the chart from months to
    # quarters must not see the reconciliation's numbers move.
    granularity: Granularity
    points: list[NetWorthPoint]

    # Δ net worth = net cash flow + currency revaluation + market appreciation
    #               + unexplained
    #
    # The first three are each computed from data; `unexplained` is the remainder by
    # construction and is therefore the only term that can be *wrong*. A non-zero
    # value is not an error to hide — it is the report telling the truth about a gap,
    # and `warnings` says what could not be converted or priced (ADR-0032 §5).
    delta_net_worth: Decimal
    net_cash_flow: Decimal
    currency_revaluation: Decimal
    market_appreciation: Decimal
    unexplained: Decimal
    # Which accounts the residual came from, largest first, and only the material
    # ones: this is what turns `unexplained` from an accusation into a lead.
    unexplained_by_account: list[UnexplainedAccount] = []
    warnings: list[str] = []

    # Always "account": an owner filter on this report selects accounts, not rows
    # (ADR-0026). Carried in the payload so the UI can say so instead of implying
    # the figures here add up with the row-scoped reports.
    attribution: str = "account"


class SpendingReport(BaseModel):
    base_currency: str
    # `start`/`end` but no `granularity`: this report is one total over the window,
    # so it has no buckets to cut and a granularity field would be a promise it
    # does not keep. The window is still echoed — it is the one field every report
    # shares, and a reader holding two of these should not have to remember which
    # range they asked for.
    start: date
    end: date
    rows: list[CategorySpendRow]
    total: Decimal
    # Always "row", for the same reason as CashFlowSeries.attribution.
    attribution: str = "row"
