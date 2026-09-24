"""Balance history typed by hand: history the provider never sent (session 06)."""

from __future__ import annotations

import pytest

# The cookie- and CSRF-aware client and per-test truncation from test_api_owners.
from tests.integration.test_api_owners import (  # noqa: F401 - fixtures
    _clean_identity,
    _signup,
    client,
)

pytestmark = pytest.mark.integration



async def test_balance_history_can_be_typed_corrected_and_removed(client):  # noqa: F811
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Chk", "type": "depository", "currency": "USD",
        "current_balance": "500", "balance_date": "2026-09-20",
    })).json()
    url = f"/accounts/{acct['id']}/balances"

    # A past day is history: the headline stays where it is.
    assert (await client.put(f"{url}/2026-06-30", json={"balance": "320.10"})).status_code == 200
    assert (await client.put(f"{url}/2026-03-31", json={"balance": "150"})).status_code == 200
    rows = (await client.get(url)).json()
    assert [r["balance_date"] for r in rows] == ["2026-09-20", "2026-06-30", "2026-03-31"]
    assert (await client.get(f"/accounts/{acct['id']}")).json()["current_balance"] == "500.0000"

    # Correcting a day replaces it; it does not add a second one.
    await client.put(f"{url}/2026-06-30", json={"balance": "330"})
    rows = (await client.get(url)).json()
    assert len(rows) == 3 and rows[1]["balance"] == "330.0000"

    # The chart reads it: net worth on that day is the typed balance.
    nw = (await client.get("/reports/net-worth", params={
        "start": "2026-06-30", "end": "2026-06-30", "granularity": "day"})).json()
    assert nw["points"][0]["net_worth"] == "330.0000"

    # Deleting the current one makes the newest remaining one current.
    assert (await client.delete(f"{url}/2026-09-20")).status_code == 204
    after = (await client.get(f"/accounts/{acct['id']}")).json()
    assert (after["current_balance"], after["balance_date"]) == ("330.0000", "2026-06-30")

    assert (await client.delete(f"{url}/2020-01-01")).status_code == 404
    assert (await client.put(f"{url}/2999-01-01", json={"balance": "1"})).status_code == 422


async def test_a_derived_account_refuses_typed_history(client):  # noqa: F811
    await _signup(client)
    acct = (await client.post("/accounts", json={
        "name": "Brokerage", "type": "investment", "currency": "USD",
    })).json()
    resp = await client.put(
        f"/accounts/{acct['id']}/balances/2026-06-30", json={"balance": "100"}
    )
    assert resp.status_code == 409
