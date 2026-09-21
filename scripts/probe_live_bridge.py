"""Ask the live bridge where its window boundaries actually are.

Everything the sync engine knows about SimpleFIN's date-range limits came from one
capture, read once, and both numbers it produced were one day out — the request
that happened to be sampled was the first one that tripped the warning, so the
threshold was recorded as that value rather than as one less. Two constants then
sat *on* the boundary instead of under it, and only a real connection could show
it: the fake provider emits no errlist, so no gate in this repo can.

This closes that hole the only way it can be closed — by asking. It asserts the
four shipped constants against the bridge itself, and it is deliberately **not** a
`verify.sh` gate or a CI job: it needs the public internet and a live third party,
and a test that fails when someone else's server is down teaches nothing.

Run it by hand when a boundary is in question, after a bridge change, or before
trusting the fixture README's numbers again:

    cd backend && PYTHONPATH=. uv run python ../scripts/probe_live_bridge.py

It uses the *public demo bridge*, which serves synthetic data and needs no
credential. Nothing here touches a real account, and nothing it prints could
identify one: counts, codes and day-offsets only — never the setup token, the
access URL, an account, a merchant or an amount.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import sys

import httpx

from app.services import simplefin, sync
from app.services.simplefin import SimpleFinProvider

#: The public developer demo. Minted per page load, single-use, synthetic data.
DEVELOPERS_URL = "https://beta-bridge.simplefin.org/info/developers"

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


async def _a_fresh_access_url() -> str:
    """Claim a demo token. Each one is single-use, so this is per request."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        page = await client.get(DEVELOPERS_URL)
    page.raise_for_status()
    tokens = re.findall(r"[A-Za-z0-9+/]{60,}={0,2}", page.text)
    if not tokens:
        raise SystemExit("the demo bridge published no setup token to claim")
    return (await SimpleFinProvider().claim(tokens[0])).access_url


def _classify(errlist: tuple[str, ...]) -> tuple[bool, bool]:
    """(warned about the range, capped) — by message, which is the point here."""
    return (
        any("recommended range" in entry for entry in errlist),
        any("limit of" in entry for entry in errlist),
    )


async def main() -> int:
    provider = SimpleFinProvider()
    now = dt.datetime.now(dt.UTC)
    failures = 0

    # Each row is one shipped constant, named, with the one thing the bridge must
    # say about it. Reading the numbers off `sync`/`simplefin` rather than typing
    # them here is what makes this a check on the code and not on this file.
    expectations = [
        (
            "FIRST_SYNC_WINDOW_DAYS",
            sync.FIRST_SYNC_WINDOW_DAYS,
            False,
            False,
            "a first sync must be clean, or every connection's first run is partial",
        ),
        (
            "RECOMMENDED_WINDOW_DAYS",
            simplefin.RECOMMENDED_WINDOW_DAYS,
            True,
            False,
            "the bridge's own figure — the warning falls *at* it, so it must warn",
        ),
        (
            "MAX_LOOKBACK_DAYS",
            sync.MAX_LOOKBACK_DAYS,
            True,
            False,
            "reach past the recommended range, but never into the cap",
        ),
        (
            "MAX_WINDOW_DAYS",
            simplefin.MAX_WINDOW_DAYS,
            False,
            True,
            "at the cap the bridge truncates on a 200, so it must be capped",
        ),
    ]

    print(f"\n  live bridge boundaries, {now.date().isoformat()}\n")
    for name, days, want_warned, want_capped, why in expectations:
        access_url = await _a_fresh_access_url()
        result = await provider.fetch_accounts(access_url, start=now - dt.timedelta(days=days))
        warned, capped = _classify(result.errlist)

        ok = warned == want_warned and capped == want_capped
        failures += not ok
        mark = f"{GREEN}ok{OFF}" if ok else f"{RED}FAIL{OFF}"
        said = "capped" if capped else ("recommended-warning" if warned else "clean")
        wanted = "capped" if want_capped else ("recommended-warning" if want_warned else "clean")
        print(f"  {mark}  {name} = {days:>3}d  ->  {said:<20} {DIM}{why}{OFF}")
        if not ok:
            print(f"        wanted {wanted!r} — the bridge changed, or the constant did")

    print()
    if failures:
        print(f"  {failures} constant(s) no longer match the bridge\n")
    else:
        print("  all four constants match the bridge\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
