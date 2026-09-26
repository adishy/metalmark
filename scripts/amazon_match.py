"""Dry-run the Amazon matcher on your own files. A spike tool; nothing is written.

    cd backend && PYTHONPATH=. uv run python ../scripts/amazon_match.py \\
        ~/Downloads/"Order History.csv" ~/Downloads/card-export.csv [--items]

The first file is Amazon's order history (Account → "Request your data" → Your
Orders; the zip holds `Retail.OrderHistory.1/Retail.OrderHistory.1.csv`, or
`Order History.csv` in newer exports). The second is one account's transactions,
exported from the app's Accounts page (columns: date, amount, description, …).

It prints counts, and with `--items` each matched charge with its items. Run it
in your own terminal: the item titles are yours, and this prints them.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.services.amazon_orders import TxnView, group_shipments, match, parse_order_history


def _ledger(path: Path) -> list[TxnView]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            TxnView(id=str(n), day=date.fromisoformat(r["date"]),
                    amount=Decimal(r["amount"]), description=r.get("description", ""))
            for n, r in enumerate(csv.DictReader(f), start=2)
        ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("orders", type=Path)
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--items", action="store_true", help="print each match's items")
    args = ap.parse_args()

    parsed = parse_order_history(args.orders.read_text(encoding="utf-8-sig"))
    for e in parsed.errors:
        print(f"orders: {e}", file=sys.stderr)
    txns = _ledger(args.ledger)
    if not txns:
        print("the ledger file has no rows", file=sys.stderr)
        return 1
    # Widened by the match window, or a box shipped the day before the export's
    # first line would be dropped though its charge is in the file.
    lo = min(t.day for t in txns) - timedelta(days=5)
    hi = max(t.day for t in txns) + timedelta(days=1)
    shipments = [s for s in group_shipments(parsed.items) if lo <= s.charge_day <= hi]
    report = match(shipments, txns)

    print(f"shipments in the ledger's date range: {len(shipments)}")
    print(f"matched:   {len(report.matches)} "
          f"({sum(m.kind == 'order' for m in report.matches)} as whole orders)")
    print(f"ambiguous: {len(report.ambiguous)}")
    print(f"unmatched shipments: {len(report.unmatched_shipments)}")
    print(f"unmatched Amazon charges in the ledger: {len(report.unmatched_txns)}")
    if args.items:
        for m in report.matches:
            print(f"\nledger line {m.txn_id} ← order {m.order_id} ({m.kind}, {m.days_apart:+d}d)")
            for i in m.items:
                print(f"    {i.total_owed:>9}  {i.title[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
