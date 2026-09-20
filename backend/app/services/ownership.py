"""The one definition of who a ledger row belongs to (ADR-0026).

Attribution precedence is ``split.owner_id → transaction.owner_id →
account.owner_id``. ``accounts.owner_id`` is NOT NULL, so the chain is *total*:
every transaction resolves to an owner, and "unattributed" is not a state the
API can produce — unset means the household's Shared owner.

Reading the effective owner is not the same as reading ``owner_id``: a row with
``owner_id IS NULL`` is not owned by nobody, it *inherits*. Anything that
aggregates or filters by owner has to go through here, or it will silently
disagree with the UI.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Owner
from app.services.errors import LedgerError


def effective_owner_id(
    *,
    split_owner_id: uuid.UUID | None,
    transaction_owner_id: uuid.UUID | None,
    account_owner_id: uuid.UUID,
) -> uuid.UUID:
    """Resolve the single owner a row is attributed to.

    ``account_owner_id`` is required rather than optional so that a caller holding
    only a transaction has to go and find the account — the alternative is a
    default that quietly invents an owner.
    """
    return split_owner_id or transaction_owner_id or account_owner_id


async def account_owner_map(
    session: AsyncSession, account_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, uuid.UUID]:
    """``account_id → owner_id`` for the household, in one query.

    Serializing a page of transactions needs every account's owner. Fetching once
    and passing the map around is the difference between one query and one per row.
    """
    stmt = select(Account.id, Account.owner_id)
    if account_ids is not None:
        ids = list(account_ids)
        if not ids:
            return {}
        stmt = stmt.where(Account.id.in_(ids))
    rows = (await session.execute(stmt)).all()
    return {row.id: row.owner_id for row in rows}


async def require_owners(
    session: AsyncSession, owner_ids: Iterable[uuid.UUID | None]
) -> None:
    """Assert that every owner id a request supplied exists *in this household*.

    Writing an owner id straight into a column leans on the foreign key, which
    answers with a 500 for an id that is unknown or belongs to another tenant — the
    same id the RLS-scoped lookup would have 404'd. Checking first, in one query for
    the whole batch, is what keeps "someone else's owner" a clean 404.
    """
    wanted = {oid for oid in owner_ids if oid is not None}
    if not wanted:
        return
    found = set(
        (await session.execute(select(Owner.id).where(Owner.id.in_(wanted)))).scalars().all()
    )
    if wanted - found:
        raise LedgerError("Owner not found", 404)
