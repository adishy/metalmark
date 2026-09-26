"""The Amazon order matcher (spike; docs/research/amazon-orders.md)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.amazon_orders import (
    TxnView,
    group_shipments,
    is_amazon,
    match,
    parse_order_history,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "amazon" / "order_history.csv"
EXPORT = FIXTURE.read_text()
HEADER = EXPORT.splitlines(keepends=True)[0]

# The fixture's orders:
# 111: one order, two boxes on different days — two card charges.
# 222: two items, two ship days, but charged once (a digital + a pre-order style
#      single charge) — only the order total appears on the card.
# 333: cancelled — never charged, must not appear.
# 444: a single box whose amount appears twice on the same day — a tie.
# 111-1 also has one row whose total is a decimal comma ("21,65").


def _t(id_: str, day: date, amount: str, desc: str = "AMZN Mktp US*2K4LJ0XY3") -> TxnView:
    return TxnView(id=id_, day=day, amount=Decimal(amount), description=desc)


def test_parse_skips_cancelled_and_reports_unreadable_money():
    result = parse_order_history(EXPORT)
    assert {i.order_id for i in result.items} == {"111-1", "222-2", "444-4"}
    # "21,65" is a decimal comma in a US file: reported, not guessed at.
    assert result.errors == ["line 3: unreadable order date or total"]
    lamp = next(i for i in result.items if i.title == "Desk lamp")
    assert lamp.total_owed == Decimal("32.48")
    assert lamp.ship_date == date(2026, 3, 5)
    ebook = next(i for i in result.items if i.title == "Ebook")
    assert ebook.ship_date is None


def test_refuses_a_file_that_is_not_an_order_history():
    result = parse_order_history("a,b\n1,2\n")
    assert result.items == []
    assert "not an Amazon order history" in result.errors[0]


def test_shipments_split_by_ship_day():
    shipments = group_shipments(parse_order_history(EXPORT).items)
    by_key = {(s.order_id, s.charge_day): s for s in shipments}
    first = by_key[("111-1", date(2026, 3, 2))]
    assert first.total == Decimal("16.24")  # the unreadable row is not in it
    assert by_key[("111-1", date(2026, 3, 5))].total == Decimal("32.48")


def test_matches_each_shipment_to_its_own_charge():
    items = parse_order_history(EXPORT).items
    shipments = group_shipments(items)
    txns = [
        _t("a", date(2026, 3, 3), "-16.24"),  # 10.83 + 5.41, posted a day later
        _t("b", date(2026, 3, 6), "-32.48"),
        _t("c", date(2026, 3, 13), "-17.98"),  # order 222 charged once: 4.99 + 12.99
        _t("d", date(2026, 3, 21), "-8.66"),
        _t("e", date(2026, 3, 21), "-8.66"),  # the tie
        _t("f", date(2026, 3, 3), "-16.24", desc="COSTCO WHSE #123"),  # not Amazon
    ]
    report = match(shipments, txns)
    got = {(m.txn_id, m.order_id, m.kind) for m in report.matches}
    assert got == {("a", "111-1", "shipment"), ("b", "111-1", "shipment"), ("c", "222-2", "order")}
    assert report.ambiguous == [("444-4", Decimal("8.66"), ["d", "e"])]
    # The Costco line is not in the Amazon pool at all.
    assert {t.id for t in report.unmatched_txns} == {"d", "e"}


def test_a_charge_outside_the_window_is_not_matched():
    shipments = group_shipments([
        i for i in parse_order_history(EXPORT).items if i.order_id == "444-4"
    ])
    report = match(shipments, [_t("late", date(2026, 3, 30), "-8.66")])
    assert report.matches == []
    assert len(report.unmatched_shipments) == 1


def test_amazon_descriptions():
    for d in ("AMZN Mktp US*2K4LJ0XY3", "Amazon.com*RT4Y12", "AMAZON PRIME*1A2B3",
              "Prime Video*9Z", "Kindle Svcs*XY"):
        assert is_amazon(d), d
    assert not is_amazon("WHOLEFDS MKT 10234")


def test_thousands_separator_is_read():
    row = 'Amazon.com,9-9,2026-01-02,USD,1200,"1,299.00",2026-01-03,TV,B08,1,Visa,Closed\n'
    text = HEADER + row
    assert parse_order_history(text).items[0].total_owed == Decimal("1299.00")
