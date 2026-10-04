"""``/agent`` — anonymized reads and opt-in entry permissions (ADR-0048/0061).

``GET /agent`` and ``GET /agent/v1`` return the catalog: every route, its
parameters and what it returns, so an agent can discover the API by calling it.
``GET /agent/v1/<path>`` is the app's own ``GET /<path>``, run as the token's
issuer in a read-only transaction and anonymized (``app/agent/dispatch.py``).

Reads require ``agent:read``; explicit POST routes require their own write scope.
Authenticated only by agent tokens, never by the session cookie.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.agent import catalog, dispatch
from app.agent.tokens import AgentPrincipal
from app.db import scoped_session
from app.deps import RequestContext
from app.logging import get_logger
from app.models import User
from app.models.agent import (
    SCOPE_ACCOUNTS_WRITE,
    SCOPE_AGENT_READ,
    SCOPE_DOCUMENTS_READ,
    SCOPE_HOLDINGS_WRITE,
    SCOPE_TRANSACTIONS_WRITE,
)
from app.schemas.agent import (
    AgentAccountCreate,
    AgentDocumentOut,
    AgentHoldingCreate,
    AgentTransactionCreate,
    CatalogOut,
)
from app.schemas.investments import HoldingOut
from app.schemas.ledger import AccountCreate, AccountOut
from app.schemas.transactions import TransactionCreate, TransactionOut
from app.services.errors import LedgerError

log = get_logger("agent")

router = APIRouter(prefix="/agent", tags=["agent"])


async def agent_principal(request: Request) -> AgentPrincipal:
    return await dispatch.principal_for(request, SCOPE_AGENT_READ)


def _links() -> dict[str, str]:
    return {"Link": f'<{catalog.AGENT_BASE}>; rel="index", <{catalog.DEBUG_BASE}>; rel="debug"'}


@router.get("", response_model=CatalogOut)
async def agent_index(_principal: AgentPrincipal = Depends(agent_principal)) -> CatalogOut:
    """The catalog of every route an agent can read. Start here."""
    return catalog.agent_catalog()


@router.get("/v1", response_model=CatalogOut)
async def agent_v1_index(_principal: AgentPrincipal = Depends(agent_principal)) -> CatalogOut:
    """The catalog of every route in v1 (the same as ``GET /agent``)."""
    return catalog.agent_catalog()


# Account files (ADR-0062). Declared before the mirror's catch-all, and not part
# of it: a file is whatever was uploaded, so nothing here is anonymized, and the
# scope that reaches it is one an owner grants knowing that.


async def _file_reader(request: Request) -> AgentPrincipal:
    principal = await dispatch.principal_for(request, SCOPE_DOCUMENTS_READ)
    if principal.role != "owner":
        raise HTTPException(
            403,
            detail={
                "code": "owner_required",
                "message": "Reading files needs a token issued by the household's owner",
                "hint": "Ask the owner to issue the token under Admin → Agent access.",
            },
        )
    return principal


def _file_refused(principal: AgentPrincipal, route: str, exc: LedgerError) -> HTTPException:
    log.info("agent.file", token_id=str(principal.token_id), route=route, status=exc.status)
    return HTTPException(exc.status, detail="File not found")


@router.get("/v1/accounts/{account_id}/documents", response_model=list[AgentDocumentOut])
async def agent_list_documents(
    account_id: uuid.UUID, principal: AgentPrincipal = Depends(_file_reader)
):
    """An account's files, with their real names. Requires documents:read.

    Not anonymized: a file name is whatever the household called the file.
    ``content`` is the path that returns the bytes.
    """
    from app.api.documents import list_account_documents

    route = "/accounts/{account_id}/documents"
    try:
        async with dispatch.agent_session(principal) as session:
            rows = await list_account_documents(session, account_id, with_digest=True)
    except LedgerError as exc:
        raise _file_refused(principal, route, exc) from None
    log.info("agent.file", token_id=str(principal.token_id), route=route, status=200)
    return [
        AgentDocumentOut(
            **row,
            content=f"{catalog.AGENT_BASE}/accounts/{account_id}/documents/{row['id']}/content",
        )
        for row in rows
    ]


@router.get("/v1/accounts/{account_id}/documents/{document_id}/content")
async def agent_read_document(
    account_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: AgentPrincipal = Depends(_file_reader),
) -> Response:
    """One file's bytes, exactly as uploaded. Requires documents:read.

    Not anonymized and not parsed: a PDF comes back as that PDF, under the media
    type it was uploaded with, and ``X-Content-SHA256`` is the digest to check it by.
    """
    from app.api.documents import get_document

    route = "/accounts/{account_id}/documents/{document_id}/content"
    try:
        async with dispatch.agent_session(principal) as session:
            row = await get_document(session, account_id, document_id, content=True)
            content, media_type, digest = row.content, row.media_type, row.sha256
    except LedgerError as exc:
        raise _file_refused(principal, route, exc) from None
    log.info(
        "agent.file", token_id=str(principal.token_id), route=route, status=200,
        row_id=str(document_id),
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-SHA256": digest,
        },
    )


@router.get("/v1/{path:path}")
async def agent_read(
    path: str, request: Request, principal: AgentPrincipal = Depends(agent_principal)
) -> JSONResponse:
    """The app's ``GET /{path}``, anonymized. See the catalog for every path."""
    result = await dispatch.run_get(
        request, principal, "/" + path, request.query_params.multi_items()
    )
    return JSONResponse(result.body, status_code=result.status, headers=_links())


# Writes are explicit routes, never a proxy accepting arbitrary unsafe methods.
# The read-only grant and debug surfaces remain unchanged (ADR-0061).


@asynccontextmanager
async def _write_context(request: Request, scope: str):
    principal = await dispatch.principal_for(request, scope)
    if principal.role != "owner":
        raise HTTPException(
            403,
            detail={
                "code": "owner_required",
                "message": "Adding entries needs a token issued by the household's owner",
                "hint": "Ask the owner to issue the token under Admin → Agent access.",
            },
        )
    async with scoped_session(principal.household_id, principal.user_id) as session:
        user = await session.get(User, principal.user_id)
        if user is None:
            raise HTTPException(401, detail="Invalid token")
        yield principal, RequestContext(session, user, principal.household_id, principal.role, "")


async def transaction_writer(request: Request):
    async with _write_context(request, SCOPE_TRANSACTIONS_WRITE) as context:
        yield context


async def holding_writer(request: Request):
    async with _write_context(request, SCOPE_HOLDINGS_WRITE) as context:
        yield context


async def account_writer(request: Request):
    async with _write_context(request, SCOPE_ACCOUNTS_WRITE) as context:
        yield context


def _refused(principal: AgentPrincipal, route: str, status: int, what: str) -> HTTPException:
    """A refusal, logged, with a message that says nothing about the household's data."""
    log.info("agent.write", token_id=str(principal.token_id), route=route, status=status)
    return HTTPException(status, detail=f"{what} could not be added")


def _db_refusal(exc: DBAPIError) -> int:
    # A unique conflict is another writer getting there first; anything else the
    # database refuses (a missing reference, a value out of range) is the request's
    # own fault. Neither is retried as an update, and neither is echoed.
    unique = isinstance(exc, IntegrityError) and getattr(exc.orig, "sqlstate", None) == "23505"
    return 409 if unique else 422


@router.post("/v1/transactions", response_model=TransactionOut, status_code=201)
async def agent_add_transaction(
    data: AgentTransactionCreate, request: Request, writer=Depends(transaction_writer)
):
    """Add one transaction. Requires transactions:write; inputs use real row IDs.

    ``amount`` is signed in the account's currency: negative is money out.
    An agent cannot set a description or merchant; its optional ``note`` is stored
    behind a fixed "Added by agent" prefix. The response is anonymized. A retried
    POST creates another entry, and so does the bank's own copy arriving later on
    a synced account — check before adding. No updates or deletes.
    """
    from app.api.transactions import create_transaction

    principal, ctx = writer
    route = "/transactions"
    try:
        result = await create_transaction(
            TransactionCreate(
                **data.model_dump(exclude={"note"}), notes=data.stored_note()
            ),
            ctx,
        )
        body = await dispatch.anonymize(request, principal, result)
    except LedgerError as exc:
        raise _refused(principal, route, exc.status, "Transaction") from None
    except DBAPIError as exc:
        raise _refused(principal, route, _db_refusal(exc), "Transaction") from None
    log.info(
        "agent.write", token_id=str(principal.token_id), route=route, status=201,
        row_id=str(result.id),
    )
    return JSONResponse(body, status_code=201, headers=_links())


@router.post("/v1/investments/holdings", response_model=HoldingOut, status_code=201)
async def agent_add_holding(
    data: AgentHoldingCreate, request: Request, writer=Depends(holding_writer)
):
    """Add one holding using an existing security ID. Requires holdings:write.

    Existing positions (including bank/history positions) are refused with 409;
    this permission cannot overwrite them. ``market_value`` is a total in the
    security's quote currency. Names, symbols and types cannot be set by an agent.
    """
    from app.api.investments import _holding_out, _record_for
    from app.services import investments as svc

    principal, ctx = writer
    route = "/investments/holdings"
    try:
        holding = await svc.upsert_holding(
            ctx.session,
            household_id=ctx.household_id,
            account_id=data.account_id,
            security_id=data.security_id,
            quantity=data.quantity,
            cost_basis=data.cost_basis,
            as_of=data.as_of,
            metadata=data,
            create_only=True,
        )
        result = _holding_out(
            await _record_for(
                ctx.session, account_id=holding.account_id, security_id=holding.security_id
            )
        )
        body = await dispatch.anonymize(request, principal, result)
    except LedgerError as exc:
        raise _refused(principal, route, exc.status, "Holding") from None
    except DBAPIError as exc:
        # A browser/sync writer may insert after the existence check. The unique
        # position constraint then rejects our insert; never retry as an update.
        raise _refused(principal, route, _db_refusal(exc), "Holding") from None
    log.info(
        "agent.write", token_id=str(principal.token_id), route=route, status=201,
        row_id=str(holding.id),
    )
    return JSONResponse(body, status_code=201, headers=_links())


@router.post("/v1/accounts", response_model=AccountOut, status_code=201)
async def agent_add_account(
    data: AgentAccountCreate, request: Request, writer=Depends(account_writer)
):
    """Add one manual account. Requires accounts:write.

    Takes a type, a currency and optionally a subtype, an opening balance and an
    owner. An agent cannot name the account or its institution: it is created as
    "Added by agent" (plus the optional ``label``) and a person renames it. The
    response is anonymized and carries the new account's id, which is what the
    transaction and holding routes take. A retried POST creates another account.
    No updates or deletes.
    """
    from app.services import ledger

    principal, ctx = writer
    route = "/accounts"
    fields = data.model_dump(exclude={"label"}, exclude_unset=True, exclude_none=True)
    try:
        account = await ledger.create_account(
            ctx.session, ctx.household_id, AccountCreate(name=data.stored_name(), **fields)
        )
        result = AccountOut.model_validate(account)
        # The name is behind the fixed prefix, so its pseudonym is its own: it
        # cannot equal a name the household wrote, whichever kind that name is.
        body = await dispatch.anonymize(request, principal, result)
    except LedgerError as exc:
        raise _refused(principal, route, exc.status, "Account") from None
    except DBAPIError as exc:
        raise _refused(principal, route, _db_refusal(exc), "Account") from None
    log.info(
        "agent.write", token_id=str(principal.token_id), route=route, status=201,
        row_id=str(account.id),
    )
    return JSONResponse(body, status_code=201, headers=_links())
