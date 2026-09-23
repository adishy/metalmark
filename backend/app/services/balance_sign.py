"""Which of a liability's stored balances predate ADR-0043's signed convention.

Before ADR-0043 the ledger held two conventions for a card or a loan at once: the
manual form stored the *amount owed* (positive), while SimpleFIN and OFX stored the
account's signed balance (debt negative) — and one account could hold both, when a
synced card's balance was then edited by hand, or a statement was imported into a
hand-made account. No column records which writer produced a row, so the rule
works per account, from what its rows say about themselves:

* **Never synced** (no ``external_key``; a disconnect keeps it): every positive row
  was typed as an amount owed. Negate them. An OFX import's rows are already
  negative and are left alone.
* **Synced, with at least one negative row**: the provider's sign is evident, so
  the positives are hand edits in the old convention. Negate them.
* **Synced, and never negative**: nothing says which convention it is — a bridge
  that sends debt as positive, or a card that has been in credit throughout. Left
  untouched rather than guessed; sync warns about a positive liability balance
  (``balance.liability_positive``), which is where a human can tell.

A genuinely overpaid card (a credit balance) on a manual account is the known
misfire: it is flipped into a small debt. It is rare, and visible on the account.

Used by the portability importer to read version-1 export documents. Migration
0007 applies the same rule in SQL — a migration must keep meaning what it meant
the day it ran, so it carries its own frozen copy, and
``test_migrations_live_data.py`` holds the two to the same answers.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

#: Account types whose balance is a debt. ``schemas.ledger.ASSET_TYPES`` is the
#: complement; spelled out here because this module is about exactly these two.
LIABILITY_TYPES = frozenset({"credit", "loan"})


def positives_are_amounts_owed(balances: Iterable[Decimal], *, ever_synced: bool) -> bool:
    """Whether this liability's positive balances were stored as "amount owed".

    ``balances`` is every balance the account holds: its snapshots and its
    ``current_balance``. When this is true, each positive one is negated and the
    negative ones are left as they are.
    """
    values = list(balances)
    if not any(v > 0 for v in values):
        return False
    return not ever_synced or any(v < 0 for v in values)
