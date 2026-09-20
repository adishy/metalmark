"""Owner CRUD (ADR-0026). Owners are household *data* — labels a charge is
attributed to, not user accounts, and owning one grants no access to anything.

Two structural rules live here rather than in the API layer because every writer
has to respect them: a household always has exactly one Shared owner (created
with the household, undeletable), and deleting an owner reassigns its rows first
— the FK is NO ACTION, so a delete that skipped the reassignment would fail
loudly instead of silently re-attributing the data.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Owner, Transaction, TransactionSplit
from app.schemas.owners import OwnerUpdate
from app.schemas.patch import is_set
from app.services.errors import LedgerError

SHARED_OWNER_NAME = "Shared"
SHARED_OWNER_KIND = "shared"
PERSON_OWNER_KIND = "person"


async def ensure_shared_owner(session: AsyncSession, household_id: uuid.UUID) -> Owner:
    """Return the household's Shared owner, creating it if it is missing.

    Household creation goes through here, so "the Shared owner exists" is an
    invariant rather than something each caller remembers to set up.
    """
    existing = (
        await session.execute(
            select(Owner).where(
                Owner.household_id == household_id, Owner.kind == SHARED_OWNER_KIND
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    owner = Owner(
        household_id=household_id, name=SHARED_OWNER_NAME, kind=SHARED_OWNER_KIND, sort=0
    )
    session.add(owner)
    await session.flush()
    return owner


async def list_owners(session: AsyncSession) -> list[Owner]:
    """Shared first, then people in their chosen order — the order every picker
    and filter chip row should render, so it is decided once, here."""
    return list(
        (
            await session.execute(
                select(Owner).order_by(
                    # Shared is the fallback rather than a peer, and reads that way.
                    (Owner.kind == SHARED_OWNER_KIND).desc(),
                    Owner.sort,
                    func.lower(Owner.name),
                )
            )
        )
        .scalars()
        .all()
    )


async def get_owner(session: AsyncSession, owner_id: uuid.UUID) -> Owner:
    """Fetch one owner. RLS scopes the lookup, so a foreign id is a 404 rather
    than a 500 from the FK when it is used."""
    owner = (
        await session.execute(select(Owner).where(Owner.id == owner_id))
    ).scalar_one_or_none()
    if owner is None:
        raise LedgerError("Owner not found", 404)
    return owner


async def _reject_duplicate_name(
    session: AsyncSession, *, household_id: uuid.UUID, name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(Owner.id).where(
        Owner.household_id == household_id, func.lower(Owner.name) == name.strip().lower()
    )
    if exclude_id is not None:
        stmt = stmt.where(Owner.id != exclude_id)
    if (await session.execute(stmt)).first() is not None:
        raise LedgerError(f'An owner named "{name.strip()}" already exists', 409)


async def _next_sort(session: AsyncSession, household_id: uuid.UUID) -> int:
    highest = (
        await session.execute(
            select(func.coalesce(func.max(Owner.sort), 0)).where(Owner.household_id == household_id)
        )
    ).scalar_one()
    return highest + 1


async def _flush_new_owner(session: AsyncSession, name: str) -> None:
    """Flush a new/renamed owner, turning a unique-name violation into a 409.

    ``_reject_duplicate_name`` catches the ordinary case with a readable message;
    this catches the race it cannot see (two requests creating the same name at
    once), which would otherwise surface as a 500.
    """
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise LedgerError(f'An owner named "{name.strip()}" already exists', 409) from exc


async def create_owner(
    session: AsyncSession, household_id: uuid.UUID, *, name: str, sort: int | None = None
) -> Owner:
    name = name.strip()
    await _reject_duplicate_name(session, household_id=household_id, name=name)
    owner = Owner(
        household_id=household_id,
        name=name,
        kind=PERSON_OWNER_KIND,  # Shared is created with the household, never via the API
        sort=sort if sort is not None else await _next_sort(session, household_id),
    )
    session.add(owner)
    await _flush_new_owner(session, name)
    return owner


async def update_owner(
    session: AsyncSession, owner_id: uuid.UUID, data: OwnerUpdate
) -> Owner:
    """Rename or reorder. The Shared owner may be renamed — "Shared" is a default,
    not a reserved word — but never re-kinded or deleted.

    ``is_set``, not ``is not None``: absent means "no change". ``OwnerUpdate``
    refuses an explicit null for either field, so the two are distinguishable and
    neither can be cleared by accident.
    """
    owner = await get_owner(session, owner_id)
    if is_set(data, "name"):
        name = data.name.strip()  # not None: OwnerUpdate rejects a null name
        await _reject_duplicate_name(
            session, household_id=owner.household_id, name=name, exclude_id=owner.id
        )
        owner.name = name
    if is_set(data, "sort"):
        owner.sort = data.sort
    await _flush_new_owner(session, owner.name)
    return owner


async def delete_owner(
    session: AsyncSession, owner: Owner, *, reassign_to: uuid.UUID | None = None
) -> dict[str, int]:
    """Reassign everything attributed to ``owner``, then delete it.

    ``reassign_to`` defaults to the household's Shared owner, which is what "I
    don't care where this goes" means. Returns the per-table counts so the UI can
    say what actually moved.
    """
    if owner.kind == SHARED_OWNER_KIND:
        raise LedgerError("The Shared owner cannot be deleted", 409)

    if reassign_to is None:
        target = await ensure_shared_owner(session, owner.household_id)
    else:
        target = await get_owner(session, reassign_to)
    if target.id == owner.id:
        raise LedgerError("Cannot reassign to the owner being deleted", 400)

    # Order matters: the FK is NO ACTION, so the delete only succeeds once nothing
    # points at the row any more.
    counts = {
        "reassigned_accounts": await _reassign(session, Account, owner.id, target.id),
        "reassigned_transactions": await _reassign(session, Transaction, owner.id, target.id),
        "reassigned_splits": await _reassign(session, TransactionSplit, owner.id, target.id),
    }
    await session.delete(owner)
    await session.flush()
    return counts


async def _reassign(session: AsyncSession, model, from_id: uuid.UUID, to_id: uuid.UUID) -> int:
    # RLS already restricts these to the household; synchronize_session=False because
    # the rows are not loaded and nothing after this reads the identity map.
    result = await session.execute(
        update(model)
        .where(model.owner_id == from_id)
        .values(owner_id=to_id)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0
