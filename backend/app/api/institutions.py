"""Institution logos (session 06, item I). See ``services/institutions``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.deps import RequestContext, get_context, require_admin
from app.schemas.institutions import FetchLogosResult, InstitutionOut, LogoUpload
from app.services import institutions as svc

router = APIRouter(prefix="/institutions", tags=["institutions"])


@router.get("", response_model=list[InstitutionOut])
async def list_institutions(ctx: RequestContext = Depends(get_context)):
    """Every institution the household's accounts name, and whether it has a logo."""
    return [
        InstitutionOut(
            name=r.name,
            key=r.key,
            fetchable=r.domain is not None,
            has_logo=r.logo is not None,
            logo_source=r.logo.source if r.logo else None,
            logo_updated_at=r.logo.updated_at if r.logo else None,
        )
        for r in await svc.in_use(ctx.session)
    ]


@router.get("/{key}/logo")
async def get_logo(key: str, ctx: RequestContext = Depends(get_context)):
    """The image. ``nosniff`` and a sandboxing CSP: served from the app's own
    origin, it must only ever be an image."""
    logo = await svc.get_logo(ctx.session, key)
    return Response(
        content=logo.data,
        media_type=logo.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.put("/{key}/logo", response_model=InstitutionOut)
async def upload_logo(key: str, body: LogoUpload, ctx: RequestContext = Depends(get_context)):
    data = svc.decode_upload(body.data_base64)
    name = body.name if svc.normalise(body.name) == key else key
    logo = await svc.save_logo(ctx.session, ctx.household_id, name=name, data=data,
                               source="uploaded")
    return InstitutionOut(
        name=logo.name,
        key=logo.key,
        fetchable=svc.domain_for(logo.name) is not None,
        has_logo=True,
        logo_source=logo.source,
        logo_updated_at=logo.updated_at,
    )


@router.delete("/{key}/logo", status_code=204)
async def delete_logo(key: str, ctx: RequestContext = Depends(get_context)):
    await svc.delete_logo(ctx.session, key)
    return Response(status_code=204)


@router.post("/fetch-logos", response_model=FetchLogosResult)
async def fetch_logos(ctx: RequestContext = Depends(require_admin)):
    """Fetch the icon of every known institution in use that has no logo yet.

    The server asks each institution's own website, once; the browser never
    does. A logo already there — fetched or uploaded — is kept.
    """
    r = await svc.fetch_missing(ctx.session, ctx.household_id)
    return FetchLogosResult(fetched=r.fetched, failed=r.failed, unknown=r.unknown, kept=r.kept)
