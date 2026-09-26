"""Amazon order history → ledger matches. A spike (docs/research/amazon-orders.md).

Pure functions, no database: parse Amazon's own order-history export, group its
item rows into the *shipments* a card is actually charged for, and propose which
bank transaction each shipment is. Nothing here writes; the output is a report a
person reads, which is the whole of what a spike should commit us to.

Why shipments and not orders: Amazon charges the card when an item ships, so a
three-item order that ships in two boxes is two bank lines. Matching on the order
total finds neither. The export has one row per item with its ship date, and the
item's ``Total Owed`` (price + tax − discounts), so a shipment's charge is the sum
of its rows' ``Total Owed`` — the order total is only the fallback for orders that
were charged once (digital orders, a single box).

Money is ``Decimal`` end to end (ADR-0005); a cell that is not plain decimal
notation is a reported error, never a guess (the importer's rule, ADR-0030).
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

# The export has shipped under two names with slightly different headers
# ("Retail.OrderHistory.1.csv" and, since 2025, "Order History.csv"). Each field
# lists every header it has been seen under; the first one present wins.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "order_id": ("Order ID",),
    "order_date": ("Order Date",),
    "ship_date": ("Ship Date",),
    "title": ("Product Name", "Title"),
    "asin": ("ASIN", "ASIN/ISBN"),
    "quantity": ("Quantity", "Original Quantity"),
    "unit_price": ("Unit Price",),
    "total_owed": ("Total Owed", "Item Total"),
    "currency": ("Currency",),
    "payment": ("Payment Instrument Type", "Payment Method Type"),
    "status": ("Order Status",),
}
_REQUIRED = ("order_id", "order_date", "total_owed")

# Descriptions banks give Amazon charges. Deliberately loose on the right: the
# suffix after `*` is a per-charge code (`AMZN Mktp US*2K4LJ0XY3`).
_AMAZON_RE = re.compile(r"\b(AMZN|AMAZON|AMZ\*|Amazon\.com|Prime Video|Kindle Svcs)", re.I)

_PLAIN_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
# A comma is accepted only as a US thousands separator (`1,234.56`). `21,65` is a
# decimal comma, and stripping it would read 21.65 as 2165.
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


@dataclass(frozen=True)
class OrderItem:
    order_id: str
    order_date: date
    ship_date: date | None
    title: str
    asin: str | None
    quantity: int
    total_owed: Decimal
    currency: str
    payment: str | None
    status: str | None


@dataclass(frozen=True)
class Shipment:
    order_id: str
    #: The day the card is charged is the ship day; an item not yet shipped
    #: (or a digital one, which has no ship date) is dated by its order.
    charge_day: date
    items: tuple[OrderItem, ...]

    @property
    def total(self) -> Decimal:
        return sum((i.total_owed for i in self.items), Decimal("0"))

    @property
    def currency(self) -> str:
        return self.items[0].currency


@dataclass(frozen=True)
class TxnView:
    """The few fields of a ledger transaction the matcher reads."""

    id: str
    day: date
    amount: Decimal  # signed; money out is negative
    description: str


@dataclass(frozen=True)
class Match:
    txn_id: str
    order_id: str
    kind: str  # "shipment" | "order"
    days_apart: int
    items: tuple[OrderItem, ...]


@dataclass
class MatchReport:
    matches: list[Match] = field(default_factory=list)
    #: A shipment with two or more equally good transactions. Reported, never
    #: picked: a coin toss would annotate a charge with someone else's items.
    #: (order id, amount, the tied transaction ids).
    ambiguous: list[tuple[str, Decimal, list[str]]] = field(default_factory=list)
    unmatched_shipments: list[Shipment] = field(default_factory=list)
    unmatched_txns: list[TxnView] = field(default_factory=list)


@dataclass
class ParseResult:
    items: list[OrderItem]
    errors: list[str]


def _money(raw: str) -> Decimal | None:
    s = raw.strip().strip("'\"").lstrip("$")
    if _THOUSANDS.match(s):
        s = s.replace(",", "")
    if not _PLAIN_DECIMAL.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:  # pragma: no cover - the regex already refused it
        return None


def _day(raw: str) -> date | None:
    s = raw.strip()
    if not s or s.lower().startswith("not "):  # "Not Available"
        return None
    try:
        # ISO with a zone (`2024-03-05T18:22:11Z`, `...+00:00`) or a bare date.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_order_history(text: str) -> ParseResult:
    """Read the export. Cancelled rows are skipped: nothing was charged."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = reader.fieldnames or []
    col = {
        key: next((h for h in names if h in headers), None) for key, names in _COLUMNS.items()
    }
    missing = [k for k in _REQUIRED if col[k] is None]
    if missing:
        return ParseResult([], [f"not an Amazon order history file (no {', '.join(missing)})"])

    def get(row: dict[str, str], key: str) -> str:
        name = col[key]
        return (row.get(name) or "") if name else ""

    items: list[OrderItem] = []
    errors: list[str] = []
    for n, row in enumerate(reader, start=2):  # line 1 is the header
        status = get(row, "status").strip() or None
        if status and status.lower() == "cancelled":
            continue
        order_day = _day(get(row, "order_date"))
        owed = _money(get(row, "total_owed"))
        if order_day is None or owed is None:
            errors.append(f"line {n}: unreadable order date or total")
            continue
        qty_raw = get(row, "quantity").strip()
        items.append(OrderItem(
            order_id=get(row, "order_id").strip(),
            order_date=order_day,
            ship_date=_day(get(row, "ship_date")),
            title=get(row, "title").strip(),
            asin=get(row, "asin").strip() or None,
            quantity=int(qty_raw) if qty_raw.isdigit() else 1,
            total_owed=owed,
            currency=(get(row, "currency").strip() or "USD").upper(),
            payment=get(row, "payment").strip() or None,
            status=status,
        ))
    return ParseResult(items, errors)


def group_shipments(items: list[OrderItem]) -> list[Shipment]:
    groups: dict[tuple[str, date], list[OrderItem]] = defaultdict(list)
    for i in items:
        groups[(i.order_id, i.ship_date or i.order_date)].append(i)
    return [
        Shipment(order_id=oid, charge_day=day, items=tuple(rows))
        for (oid, day), rows in sorted(groups.items(), key=lambda kv: kv[0][1])
    ]


def is_amazon(description: str) -> bool:
    return bool(_AMAZON_RE.search(description or ""))


def match(
    shipments: list[Shipment],
    txns: list[TxnView],
    *,
    before_days: int = 1,
    after_days: int = 5,
) -> MatchReport:
    """Propose one transaction per shipment: same amount to the cent, posted
    within ``[charge_day - before_days, charge_day + after_days]``.

    A bank posts a day or three after the charge; the one day *before* covers
    time zones (Amazon's dates are UTC, a card's are local). Exact cents, no
    tolerance: an Amazon charge is a sum Amazon computed, and a near-miss that
    matched would be a different charge that happens to be close.

    Two passes. Shipments first; then, for orders none of whose shipments
    matched, the order total — an order charged once. Each transaction is used
    at most once, so the second pass cannot claim a charge the first one took.
    """
    report = MatchReport()
    pool = [t for t in txns if t.amount < 0 and is_amazon(t.description)]
    used: set[str] = set()

    def candidates(total: Decimal, day: date) -> list[TxnView]:
        lo, hi = day - timedelta(days=before_days), day + timedelta(days=after_days)
        return [
            t for t in pool
            if t.id not in used and -t.amount == total and lo <= t.day <= hi
        ]

    def settle(total: Decimal, day: date, order_id: str, kind: str,
               items: tuple[OrderItem, ...]) -> bool:
        found = candidates(total, day)
        if not found:
            return False
        found.sort(key=lambda t: abs((t.day - day).days))
        best = abs((found[0].day - day).days)
        tied = [t for t in found if abs((t.day - day).days) == best]
        if len(tied) > 1:
            report.ambiguous.append((order_id, total, [t.id for t in tied]))
            return True  # handled: reported, not assigned
        used.add(found[0].id)
        report.matches.append(Match(found[0].id, order_id, kind, (found[0].day - day).days, items))
        return True

    unsettled: list[Shipment] = []
    for s in shipments:
        if not settle(s.total, s.charge_day, s.order_id, "shipment", s.items):
            unsettled.append(s)

    by_order: dict[str, list[Shipment]] = defaultdict(list)
    for s in unsettled:
        by_order[s.order_id].append(s)
    all_by_order: dict[str, list[Shipment]] = defaultdict(list)
    for s in shipments:
        all_by_order[s.order_id].append(s)
    for oid, parts in by_order.items():
        # Only when *every* shipment of the order is unsettled: an order with
        # one box matched and one not is a missing charge, not a single charge.
        if len(parts) != len(all_by_order[oid]) or len(parts) == 1:
            report.unmatched_shipments.extend(parts)
            continue
        items = tuple(i for p in parts for i in p.items)
        total = sum((p.total for p in parts), Decimal("0"))
        if not settle(total, min(p.charge_day for p in parts), oid, "order", items):
            report.unmatched_shipments.extend(parts)

    report.unmatched_txns = [t for t in pool if t.id not in used]
    return report
