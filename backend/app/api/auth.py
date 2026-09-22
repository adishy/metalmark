from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import select

from app.db import unscoped_session
from app.deps import SESSION_COOKIE, RequestContext, get_context
from app.models import Household
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    SignupRequest,
    UserResponse,
)
from app.services import auth as svc
from app.settings import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    # `Secure` is the difference between "logged in" and "logged in until the next
    # request" on an origin whose browser will not keep the cookie, and the
    # deployment on a private network with no TLS is exactly that origin — so the
    # flag is a deployment decision (ADR-0042) rather than a consequence of
    # METALMARK_ENV. The derivation it defaults to is the one that was here.
    secure = get_settings().cookie_is_secure
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


async def _me_payload(session, user, membership) -> MeResponse:
    household = (
        await session.execute(select(Household).where(Household.id == membership.household_id))
    ).scalar_one()
    return MeResponse(
        user=UserResponse(
            id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin
        ),
        household_id=household.id,
        household_name=household.name,
        base_currency=household.base_currency,
        role=membership.role,
        csrf_token="",  # filled by caller from the session record
    )


@router.post("/signup", response_model=MeResponse, status_code=201)
async def signup(req: SignupRequest, response: Response) -> MeResponse:
    async with unscoped_session() as session:
        try:
            user = await svc.signup(
                session,
                email=req.email,
                display_name=req.display_name,
                password=req.password,
                household_name=req.household_name,
            )
            token, sess, _ = await svc.login(session, email=req.email, password=req.password)
            membership = await svc.membership_for(session, user.id)
        except svc.AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        payload = await _me_payload(session, user, membership)
    _set_session_cookie(response, token)
    payload.csrf_token = sess.csrf_token
    return payload


@router.post("/login", response_model=MeResponse)
async def login(req: LoginRequest, response: Response) -> MeResponse:
    async with unscoped_session() as session:
        try:
            token, sess, user = await svc.login(session, email=req.email, password=req.password)
        except svc.AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        membership = await svc.membership_for(session, user.id)
        if membership is None:
            raise HTTPException(status_code=403, detail="User is not in a household")
        payload = await _me_payload(session, user, membership)
    _set_session_cookie(response, token)
    payload.csrf_token = sess.csrf_token
    return payload


@router.post("/logout", status_code=204)
async def logout(
    metalmark_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Response:
    # Revoke the server-side session (logout is real, not just cookie clearing).
    if metalmark_session:
        async with unscoped_session() as session:
            await svc.logout(session, metalmark_session)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/me", response_model=MeResponse)
async def me(ctx: RequestContext = Depends(get_context)) -> MeResponse:
    membership = await svc.membership_for(ctx.session, ctx.user.id)
    payload = await _me_payload(ctx.session, ctx.user, membership)
    payload.csrf_token = ctx.csrf_token
    return payload
