"""Institution logos: uploaded by a person, or fetched once from the bank's own site.

**The browser never fetches a logo from the web.** Which banks a household uses
is not something to tell a third party (DESIGN §4.15, ADR-0002), so the account
marks never call a favicon service. A logo reaches the page from this server,
out of the household's own table, and gets there one of two ways:

* **Uploaded** by a person — any PNG, JPEG, GIF, WebP or ICO up to 512 KB.
* **Fetched** when an admin asks: for an institution this module knows the
  website of, the server asks *that institution's own site* for its touch icon
  — the square, full-colour mark phones put on a home screen — once, and
  stores it. The request goes to the bank the household already banks with,
  and to nobody else.

**No SVG.** An SVG is a document that can carry script, and these are served
from the app's own origin; a raster image cannot. The bytes are checked by their
signature, not by the name or the header a server sent.
"""

from __future__ import annotations

import base64
import contextlib
import re
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Account, AccountConnection
from app.models.institution import InstitutionLogo
from app.services.errors import LedgerError

log = get_logger("institutions")

MAX_BYTES = 512 * 1024
TIMEOUT_SECONDS = 10.0
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) MetalMark-Money/1 (logo fetch)"

# Institutions whose website is known, by the words their names carry. Order
# matters where one name contains another ("capital one" before "one").
KNOWN: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), domain)
    for p, domain in [
        (r"\bchase\b|jp ?morgan", "chase.com"),
        (r"capital ?one", "capitalone.com"),
        (r"fidelity", "fidelity.com"),
        (r"american express|\bamex\b", "americanexpress.com"),
        (r"bank of america|\bboa\b|\bbofa\b", "bankofamerica.com"),
        (r"merrill", "merrill.com"),
        (r"wells ?fargo", "wellsfargo.com"),
        (r"\bciti(bank)?\b", "citi.com"),
        (r"discover", "discover.com"),
        (r"schwab", "schwab.com"),
        (r"vanguard", "vanguard.com"),
        (r"robinhood", "robinhood.com"),
        (r"\bu\.? ?s\.? bank\b|usbank", "usbank.com"),
        (r"\bally\b", "ally.com"),
        (r"\bsofi\b", "sofi.com"),
        (r"marcus|goldman", "marcus.com"),
        (r"apple card|apple cash|apple savings", "apple.com"),
        (r"wealthfront", "wealthfront.com"),
        (r"betterment", "betterment.com"),
        (r"e ?\*? ?trade", "etrade.com"),
        (r"morgan stanley", "morganstanley.com"),
        (r"\bpnc\b", "pnc.com"),
        (r"\btd\b", "td.com"),
        (r"navy federal", "navyfederal.org"),
        (r"\busaa\b", "usaa.com"),
        (r"paypal", "paypal.com"),
        (r"venmo", "venmo.com"),
        (r"coinbase", "coinbase.com"),
        (r"interactive brokers|\bibkr\b", "interactivebrokers.com"),
        (r"\bm1\b", "m1.com"),
        (r"\bchime\b", "chime.com"),
        (r"mercury", "mercury.com"),
        (r"\bbilt\b", "biltrewards.com"),
        (r"synchrony", "synchrony.com"),
        (r"barclays", "barclaysus.com"),
        (r"citizens", "citizensbank.com"),
        (r"treasury ?direct", "treasurydirect.gov"),
        (r"\bhdfc\b", "hdfcbank.com"),
        (r"\bicici\b", "icicibank.com"),
        (r"\bsbi\b|state bank of india", "sbi.co.in"),
        (r"\baxis\b", "axisbank.com"),
        (r"kotak", "kotak.com"),
        (r"wise\b|transferwise", "wise.com"),
        (r"revolut", "revolut.com"),
    ]
]

_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
]


def normalise(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.casefold()).split())


def domain_for(name: str) -> str | None:
    key = normalise(name)
    for pattern, domain in KNOWN:
        if pattern.search(key):
            return domain
    return None


def sniff(data: bytes) -> str | None:
    """The image type the bytes themselves say, or None if they are not one we serve."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for signature, content_type in _SIGNATURES:
        if data.startswith(signature):
            return content_type
    return None


# ---- the store ------------------------------------------------------------------


@dataclass
class InstitutionRow:
    name: str
    key: str
    domain: str | None
    logo: InstitutionLogo | None


async def in_use(session: AsyncSession) -> list[InstitutionRow]:
    """Every institution the household's accounts and connections name, with its logo."""
    names: dict[str, str] = {}
    for (name,) in (
        await session.execute(select(Account.institution).where(Account.institution.is_not(None)))
    ).all():
        names.setdefault(normalise(name), name)
    for (name,) in (
        await session.execute(
            select(AccountConnection.org_name).where(AccountConnection.org_name.is_not(None))
        )
    ).all():
        names.setdefault(normalise(name), name)
    logos = {
        logo.key: logo
        for logo in (await session.execute(select(InstitutionLogo))).scalars().all()
    }
    # A logo whose institution no account names any more is still listed, so it
    # can be seen and removed rather than lingering invisibly.
    for key, logo in logos.items():
        names.setdefault(key, logo.name)
    return sorted(
        (
            InstitutionRow(name=name, key=key, domain=domain_for(name), logo=logos.get(key))
            for key, name in names.items()
            if key
        ),
        key=lambda r: r.name.casefold(),
    )


async def get_logo(session: AsyncSession, key: str) -> InstitutionLogo:
    logo = (
        await session.execute(select(InstitutionLogo).where(InstitutionLogo.key == key))
    ).scalar_one_or_none()
    if logo is None:
        raise LedgerError("No logo for that institution", 404)
    return logo


async def save_logo(
    session: AsyncSession, household_id: uuid.UUID, *, name: str, data: bytes, source: str
) -> InstitutionLogo:
    if len(data) > MAX_BYTES:
        raise LedgerError("A logo can be at most 512 KB", 413)
    content_type = sniff(data)
    if content_type is None:
        raise LedgerError("A logo must be a PNG, JPEG, GIF, WebP or ICO image", 415)
    key = normalise(name)
    if not key:
        raise LedgerError("An institution needs a name", 422)
    logo = (
        await session.execute(select(InstitutionLogo).where(InstitutionLogo.key == key))
    ).scalar_one_or_none()
    if logo is None:
        logo = InstitutionLogo(household_id=household_id, key=key, name=name)
        session.add(logo)
    logo.data = data
    logo.content_type = content_type
    logo.source = source
    await session.flush()
    await session.refresh(logo)  # the server-set timestamps, for the response
    return logo


def decode_upload(data_base64: str) -> bytes:
    """A base64 body, with or without a ``data:image/…;base64,`` prefix."""
    payload = data_base64.split(",", 1)[1] if data_base64.startswith("data:") else data_base64
    try:
        return base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise LedgerError("The image is not valid base64", 422) from exc


async def delete_logo(session: AsyncSession, key: str) -> None:
    logo = await get_logo(session, key)
    await session.delete(logo)
    await session.flush()


# ---- fetching -------------------------------------------------------------------


class _IconLinks(HTMLParser):
    """``<link rel="…icon…" href sizes>`` from a page's head, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, int]] = []  # (rel, href, largest size)

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower()
        if "icon" not in rel or not a.get("href"):
            return
        sizes = [int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))]
        self.links.append((rel, a["href"], max(sizes, default=0)))


def _candidates(base: str, html: str) -> list[str]:
    parser = _IconLinks()
    # A page we cannot parse just has no links.
    with contextlib.suppress(Exception):
        parser.feed(html)
    # Touch icons first (square, full colour, usually 180 px), then the largest
    # plain icon, then the conventional paths every site answers.
    ranked = sorted(
        parser.links,
        key=lambda link: ("apple-touch-icon" not in link[0], -link[2]),
    )
    urls = [urljoin(base, href) for _rel, href, _size in ranked]
    urls += [urljoin(base, "/apple-touch-icon.png"), urljoin(base, "/favicon.ico")]
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u)) and not u.endswith(".svg")]


async def fetch_icon(client: httpx.AsyncClient, domain: str) -> bytes | None:
    base = f"https://www.{domain}/" if domain.count(".") == 1 else f"https://{domain}/"
    try:
        page = await client.get(base)
        html = page.text if page.status_code < 400 else ""
        base = str(page.url)
    except httpx.HTTPError:
        html = ""
    for url in _candidates(base, html):
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            continue
        if resp.status_code >= 400 or len(resp.content) > MAX_BYTES:
            continue
        if sniff(resp.content):
            return resp.content
    return None


@dataclass
class FetchResult:
    fetched: list[str]
    failed: list[str]
    unknown: list[str]
    kept: int


async def fetch_missing(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Fetch a logo for every known institution in use that has none.

    Never replaces a logo — an uploaded one especially is a person's choice.
    """
    result = FetchResult(fetched=[], failed=[], unknown=[], kept=0)
    rows = await in_use(session)
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        transport=transport,
    ) as client:
        for row in rows:
            if row.logo is not None:
                result.kept += 1
                continue
            if row.domain is None:
                result.unknown.append(row.name)
                continue
            data = await fetch_icon(client, row.domain)
            if data is None:
                result.failed.append(row.name)
                log.info("institutions.logo_fetch_failed", domain=row.domain)
                continue
            await save_logo(session, household_id, name=row.name, data=data, source="fetched")
            result.fetched.append(row.name)
    return result
