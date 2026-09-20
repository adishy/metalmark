"""End-to-end walkthrough of the M1a paths the Playwright suite does not reach.

Runs against a live stack over real HTTP, as the dev owner, and prints a PASS/FAIL
line per assertion. Everything it creates is tagged with a run id, so a failed run
is identifiable in the UI afterwards.

It complements the other suites rather than repeating them. Playwright covers the
paths a *user* clicks; this covers the ones that are easier to state precisely as
HTTP — the ownership chain and its filters, the split-sum invariant, what a
transfer link does to cash flow and revaluation, rule provenance and idempotency,
CSV re-import and near-duplicates, and the signup/household rules. The pytest
suite covers the same services in-process against a throwaway database; this is
the check that the same things are true of the assembled app.

Prerequisites: a running stack whose database has been migrated and seeded. Run
it through ``scripts/verify.sh walkthrough``, which documents every gate.

Environment:
  WALKTHROUGH_BASE_URL  default http://localhost:8000
  WALKTHROUGH_EMAIL     default owner@example.com
  WALKTHROUGH_PASSWORD  default devpassword123
"""

import datetime as dt
import io
import json
import os
import sys
import uuid

import httpx

BASE = os.environ.get("WALKTHROUGH_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("WALKTHROUGH_EMAIL", "owner@example.com")
PASSWORD = os.environ.get("WALKTHROUGH_PASSWORD", "devpassword123")
RUN = uuid.uuid4().hex[:6]
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}  {detail}")
    else:
        bad += 1
        print(f"  FAIL  {name}  {detail}")
    return cond


def section(t):
    print(f"\n=== {t}")


def body(r, n=300):
    return r.text[:n].replace("\n", " ")


def day(offset):
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


c = httpx.Client(base_url=BASE, timeout=60.0, follow_redirects=True)

# ---------------------------------------------------------------- login
section("login")
r = c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
if not check("login as dev owner", r.status_code == 200, f"{r.status_code} {body(r)}"):
    print(f"\nCannot continue without a login. Is the stack up, migrated and seeded? "
          f"(base_url={BASE})", file=sys.stderr)
    sys.exit(1)
me = r.json()
C = {"X-CSRF-Token": me["csrf_token"]}
check("me carries csrf + household", bool(me.get("csrf_token")) and bool(me.get("household_id")),
      f"role={me.get('role')} base={me.get('base_currency')}")

# ---------------------------------------------------------------- owners
section("owners")
r = c.post("/owners", json={"name": f"Alice {RUN}"}, headers=C)
check("create person owner", r.status_code == 201, f"{r.status_code} {body(r)}")
alice = r.json()["id"]
r = c.post("/owners", json={"name": f"Beth {RUN}"}, headers=C)
beth = r.json()["id"]

r = c.get("/owners")
shared = [o for o in r.json() if o["kind"] == "shared"]
check("exactly one shared owner exists", len(shared) == 1, f"{[o['name'] for o in r.json()]}")
shared_id = shared[0]["id"]

r = c.delete(f"/owners/{shared_id}", headers=C)
check("shared owner is undeletable", r.status_code == 409, f"{r.status_code}")

r = c.post("/owners", json={"name": f"alice {RUN}"}, headers=C)
check("owner names are case-insensitively unique", r.status_code == 409, f"{r.status_code}")

foreign_owner = str(uuid.uuid4())
r = c.post("/transactions", json={
    "account_id": str(uuid.uuid4()), "amount": "-1.00",
    "transacted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "owner_id": foreign_owner}, headers=C)
check("foreign owner id is refused, not a 500", r.status_code in (404, 422), f"{r.status_code}")

# ---------------------------------------------------------------- accounts
section("accounts")
r = c.post("/accounts", json={
    "name": f"Alice Checking {RUN}", "type": "depository",
    "currency": me["base_currency"], "current_balance": "1000.00", "owner_id": alice}, headers=C)
check("create account owned by Alice", r.status_code == 201, f"{r.status_code} {body(r)}")
acct_a = r.json()["id"]

r = c.post("/accounts", json={
    "name": f"Beth EUR {RUN}", "type": "depository",
    "currency": "EUR", "current_balance": "500.00", "owner_id": beth}, headers=C)
check("create EUR account owned by Beth", r.status_code == 201, f"{r.status_code} {body(r)}")
acct_b = r.json()["id"]

r = c.patch(f"/accounts/{acct_a}", json={"owner_id": None}, headers=C)
check("account owner cannot be cleared (NOT NULL)", r.status_code == 422, f"{r.status_code}")

r = c.post("/accounts", json={
    "name": f"Household Joint {RUN}", "type": "depository",
    "currency": me["base_currency"], "current_balance": "0.00", "owner_id": shared_id}, headers=C)
check("create account owned by Shared", r.status_code == 201, f"{r.status_code} {body(r)}")
acct_shared = r.json()["id"]

r = c.get("/accounts", params={"owner_id": alice})
names = [a["name"] for a in r.json()]
check("account filter narrows to the owner",
      f"Alice Checking {RUN}" in names and f"Beth EUR {RUN}" not in names, f"{names}")

# ---------------------------------------------------------------- transactions + owner assign/clear
section("transactions / owner assign + clear")
r = c.post("/transactions", json={
    "account_id": acct_a, "amount": "-42.00", "merchant": f"Cafe {RUN}",
    "transacted_at": dt.datetime.combine(dt.date.today(), dt.time(12)).astimezone().isoformat()},
    headers=C)
check("create transaction (inherits account owner)", r.status_code == 201, f"{r.status_code} {body(r)}")
t1 = r.json()
check("effective owner falls back to the account's", t1["effective_owner_id"] == alice,
      f"effective={t1['effective_owner_id']} explicit={t1['owner_id']}")

r = c.patch(f"/transactions/{t1['id']}", json={"owner_id": beth}, headers=C)
check("assign an explicit owner", r.json()["owner_id"] == beth and r.json()["effective_owner_id"] == beth,
      f"{body(r)}")

r = c.patch(f"/transactions/{t1['id']}", json={"merchant": f"Cafe {RUN} (renamed)"}, headers=C)
check("PATCH omitting owner_id leaves it alone", r.json()["owner_id"] == beth, f"{body(r)}")

r = c.patch(f"/transactions/{t1['id']}", json={"owner_id": None}, headers=C)
check("PATCH explicit null clears the owner back to inherit",
      r.json()["owner_id"] is None and r.json()["effective_owner_id"] == alice, f"{body(r)}")

r = c.get("/transactions", params={"owner_id": beth})
ids = [t["id"] for t in r.json()["items"]]
check("owner filter excludes the reverted row", t1["id"] not in ids, f"{len(ids)} rows")

# ---------------------------------------------------------------- splits with owners
section("splits with owners")
# On a SHARED-owned account, so the parent's own chain resolves to Shared and the
# only reason an owner filter can match it is a split child. On Alice's account the
# filter would match by inheritance and the test would prove nothing.
r = c.post("/transactions", json={
    "account_id": acct_shared, "amount": "-100.00", "merchant": f"Joint {RUN}",
    "transacted_at": dt.datetime.combine(dt.date.today(), dt.time(12)).astimezone().isoformat()},
    headers=C)
parent = r.json()["id"]
check("parent on a Shared account resolves to Shared",
      r.json()["effective_owner_id"] == shared_id, f"{r.json()['effective_owner_id']}")

r = c.put(f"/transactions/{parent}/splits", json=[
    {"amount": "-70.00", "owner_id": alice},
    {"amount": "-30.00", "owner_id": beth},
], headers=C)
check("split a transaction across two owners", r.status_code in (200, 201), f"{r.status_code} {body(r)}")
splits = r.json().get("splits", []) if r.status_code in (200, 201) else []
check("each split reports its OWN effective owner",
      sorted(s.get("effective_owner_id") for s in splits) == sorted([alice, beth]),
      f"{[s.get('effective_owner_id') for s in splits]}")

for who, name, want in ((alice, "Alice", True), (beth, "Beth", True), (shared_id, "Shared", False)):
    r = c.get("/transactions", params={"owner_id": who})
    hit = any(t["id"] == parent for t in r.json()["items"])
    check(f"filtering by {name} {'returns' if want else 'excludes'} the split parent",
          hit == want, f"returned={hit}")

# A split parent's amount is a mirror of its children, which is what every report
# sums. Editing the parent alone would succeed while changing nothing a user sees.
r = c.patch(f"/transactions/{parent}", json={"amount": "-500.00"}, headers=C)
check("a split parent's amount cannot be edited directly", r.status_code == 409,
      f"{r.status_code} {body(r)}")
r = c.get(f"/transactions/{parent}")
check("...and the parent still agrees with its children",
      r.json()["amount"] == "-100.0000"
      and sum(float(s["amount"]) for s in r.json()["splits"]) == -100.0,
      f"parent={r.json()['amount']} children={[s['amount'] for s in r.json()['splits']]}")

child = splits[0]["id"] if splits else None
if child:
    r = c.put(f"/transactions/{child}/splits", json=[{"amount": "-50.00"}], headers=C)
    check("a split child cannot itself be split", 400 <= r.status_code < 500, f"{r.status_code} {body(r)}")

r = c.put(f"/transactions/{parent}/splits", json=[
    {"amount": "-50.00", "owner_id": alice}, {"amount": "-50.00", "owner_id": beth}], headers=C)
check("re-splitting replaces rather than appends", r.status_code in (200, 201), f"{r.status_code}")
r = c.get(f"/transactions/{parent}")
check("parent still shows exactly two children", len(r.json().get("splits", [])) == 2,
      f"{len(r.json().get('splits', []))}")

r = c.put(f"/transactions/{parent}/splits", json=[], headers=C)
check("an empty split list restores a plain row",
      r.status_code in (200, 201) and not r.json().get("splits"), f"{r.status_code} {body(r)}")

# ---------------------------------------------------------------- fx rate
section("fx rate (supplied here, not by another suite)")
# The cross-currency checks below are meaningless without a rate, and depending on
# an earlier Playwright run to have created one would make this suite pass or fail
# by ordering. So it creates the rate it needs.
r = c.post("/fx-rates", json={
    "base_currency": me["base_currency"], "quote_currency": "EUR",
    "rate_date": dt.date.today().isoformat(), "rate": "0.9250"}, headers=C)
check("upsert a rate for the account's currency", r.status_code in (200, 201),
      f"{r.status_code} {body(r)}")

# ---------------------------------------------------------------- cross-currency transfer
section("cross-currency transfer link / unlink")
r = c.post("/transactions", json={
    "account_id": acct_a, "amount": "-100.00", "merchant": f"Transfer to Beth {RUN}",
    "transacted_at": dt.datetime.combine(dt.date.today(), dt.time(9)).astimezone().isoformat()}, headers=C)
out_txn = r.json()["id"]
r = c.post("/transactions", json={
    "account_id": acct_b, "amount": "90.00", "merchant": f"Transfer from Alice {RUN}",
    "transacted_at": dt.datetime.combine(dt.date.today(), dt.time(9)).astimezone().isoformat()}, headers=C)
in_txn = r.json()["id"]

r = c.get("/transactions/transfer-candidates", params={"txn_id": out_txn, "days": 5})
check("transfer candidates return the EUR counterpart", r.status_code == 200, f"{r.status_code} {body(r)}")


MONTH = dt.date.today().strftime("%Y-%m")


def month_flow(income=False):
    r = c.get("/reports/cash-flow", params={"start": day(-40), "end": day(1)})
    if r.status_code != 200:
        return None
    row = next((m for m in r.json() if m["month"] == MONTH), None)
    return float(row["income"] if income else row["expense"]) if row else 0.0


def net_worth_parts():
    r = c.get("/reports/net-worth", params={"start": day(-40), "end": day(1)})
    if r.status_code != 200:
        return None
    d = r.json()
    return (float(d["delta_net_worth"]), float(d["net_cash_flow"]),
            float(d["currency_revaluation"]))


def legs():
    """The two legs' base amounts, as the ledger booked them."""
    return (abs(float(c.get(f"/transactions/{out_txn}").json()["base_amount"])),
            abs(float(c.get(f"/transactions/{in_txn}").json()["base_amount"])))


out_base, in_base = legs()
check("both legs converted to base (a rate exists)", out_base > 0 and in_base > 0,
      f"out={out_base} in={in_base}")
exp_before, inc_before = month_flow(), month_flow(income=True)
nw_before = net_worth_parts()
if nw_before:
    check("net worth identity holds before linking (ΔNW = CF + revaluation)",
          abs(nw_before[0] - (nw_before[1] + nw_before[2])) < 0.01,
          f"ΔNW={nw_before[0]} CF={nw_before[1]} reval={nw_before[2]}")

r = c.post("/transactions/transfers", json={"from_txn_id": out_txn, "to_txn_id": in_txn}, headers=C)
check("link a cross-currency transfer", r.status_code in (200, 201), f"{r.status_code} {body(r)}")
if r.status_code in (200, 201):
    tr = r.json()
    group = tr["transfer_group_id"]
    residual = float(tr.get("fx_cost_base") or 0)
    check("the residual is the real conversion gap, not zero",
          residual != 0, f"fx_cost_base={residual} ({out_base} out vs {in_base} in, both base)")

    # "Excluded from cash flow" means neither leg is a P&L event any more — the
    # outgoing row is not an expense and the incoming row is not income. Stated on
    # net alone this is ambiguous (the two legs never cancel: their difference IS
    # the FX cost), so assert it per side.
    exp_after, inc_after = month_flow(), month_flow(income=True)
    # Expenses are booked negative and income positive, so a removed expense raises
    # the expense figure and a removed income lowers the income figure. Assert the
    # size of the move, per side.
    check("linking removes the outgoing leg from expenses",
          exp_after is not None and abs(abs(exp_after - exp_before) - out_base) < 0.0001,
          f"expense {exp_before} -> {exp_after}, leg {out_base}")
    check("linking removes the incoming leg from income",
          inc_after is not None and abs(abs(inc_after - inc_before) - in_base) < 0.0001,
          f"income {inc_before} -> {inc_after}, leg {in_base}")

    nw_after = net_worth_parts()
    if nw_after and nw_before:
        # No money left the household, so net worth is unmoved by the *link* — but
        # the conversion cost it reveals must still be accounted for somewhere, and
        # it is: it moves out of cash flow and into revaluation.
        check("linking does not change net worth",
              abs(nw_after[0] - nw_before[0]) < 0.01,
              f"ΔNW {nw_before[0]} -> {nw_after[0]}")
        check("net worth identity still holds after linking",
              abs(nw_after[0] - (nw_after[1] + nw_after[2])) < 0.01,
              f"ΔNW={nw_after[0]} CF={nw_after[1]} reval={nw_after[2]}")
        # Unlinked, the two legs leave a hole in cash flow (you sent 100 and booked
        # 90 * 0.925 back). Linked, they leave cash flow entirely and the same hole
        # reappears as revaluation — the conversion cost, no longer disguised as
        # ordinary spending.
        check("the conversion cost moves from cash flow into revaluation",
              abs((nw_after[1] - nw_before[1]) - (out_base - in_base)) < 0.01
              and abs((nw_after[2] - nw_before[2]) + (out_base - in_base)) < 0.01,
              f"CF {nw_before[1]} -> {nw_after[1]}, reval {nw_before[2]} -> {nw_after[2]}")

    r = c.delete(f"/transactions/transfers/{group}", headers=C)
    check("unlink the transfer", r.status_code in (200, 204), f"{r.status_code} {body(r)}")
    check("unlinking puts both legs back into cash flow",
          abs(month_flow() - exp_before) < 0.0001 and abs(month_flow(income=True) - inc_before) < 0.0001,
          f"expense={month_flow()} income={month_flow(income=True)}")

# ---------------------------------------------------------------- rules
section("rules: build, apply, idempotency")
r = c.post("/rules", json={
    "name": f"Tag cafés {RUN}", "priority": 10,
    "conditions": {"merchant_contains": f"Cafe {RUN}"},
    "actions": {"set_owner_id": beth, "mark_reviewed": True}}, headers=C)
check("create a rule", r.status_code == 201, f"{r.status_code} {body(r)}")

r = c.post("/rules", json={"name": "bad", "conditions": {"nonsense": "x"}}, headers=C)
check("unknown condition key is rejected (422)", r.status_code == 422, f"{r.status_code}")

r = c.post("/rules/apply", json={}, headers=C)
check("apply to existing rows", r.status_code == 200, f"{r.status_code} {body(r)}")
r2 = c.post("/rules/apply", json={}, headers=C)
check("apply is idempotent (second run updates nothing)",
      r2.status_code == 200 and r2.json().get("updated") == 0, f"{body(r2)}")

r = c.get(f"/transactions/{t1['id']}")
row = r.json()
# The user explicitly cleared this row's owner, so provenance says "user" and the
# rule's set_owner_id must NOT win — while its mark_reviewed, which nothing has
# claimed, must. One rule, two actions, opposite outcomes, both correct.
check("rule applies mark_reviewed to the row", row.get("review_status") == "reviewed",
      f"review={row.get('review_status')}")
check("rule does NOT override the user's explicit owner clear", row.get("owner_id") is None,
      f"owner_id={row.get('owner_id')}")

r = c.patch(f"/transactions/{t1['id']}", json={"owner_id": alice}, headers=C)
r = c.post("/rules/apply", json={}, headers=C)
r = c.get(f"/transactions/{t1['id']}")
check("a user-set owner survives re-apply (provenance wins)", r.json()["owner_id"] == alice, f"{body(r)}")

# ---------------------------------------------------------------- CSV import
section("CSV import: preview, commit, re-import, near-duplicate")
csv_text = (
    "Date,Description,Amount\n"
    f"{day(-3)},IMPORT-ONE-{RUN},-11.11\n"
    f"{day(-2)},IMPORT-TWO-{RUN},-22.22\n"
    f"{day(-1)},IMPORT-THREE-{RUN},-33.33\n"
)
files = {"file": ("stmt.csv", io.BytesIO(csv_text.encode()), "text/csv")}
r = c.post("/import/csv/preview", files=files, headers=C)
check("preview a CSV", r.status_code == 200, f"{r.status_code} {body(r)}")
mapping = r.json().get("suggested") if r.status_code == 200 else None
print(f"        suggested mapping: {json.dumps(mapping)[:200]}")

check("preview suggests a usable mapping", bool(mapping), f"{mapping}")

files = {"file": ("stmt.csv", io.BytesIO(csv_text.encode()), "text/csv")}
r = c.post("/import/csv/commit", files=files,
           data={"account_id": acct_a, "mapping": json.dumps(mapping)}, headers=C)
check("commit inserts all three rows", r.status_code == 200 and r.json().get("inserted") == 3,
      f"{r.status_code} {body(r)}")

files = {"file": ("stmt.csv", io.BytesIO(csv_text.encode()), "text/csv")}
r = c.post("/import/csv/commit", files=files,
           data={"account_id": acct_a, "mapping": json.dumps(mapping)}, headers=C)
check("re-importing the same file skips every row",
      r.status_code == 200 and r.json().get("inserted") == 0 and r.json().get("skipped") == 3,
      f"{body(r)}")

near = (
    "Date,Description,Amount\n"
    f"{day(-1)},IMPORT-THREE-{RUN} (pending),-33.33\n"
)
files = {"file": ("stmt.csv", io.BytesIO(near.encode()), "text/csv")}
r = c.post("/import/csv/commit", files=files,
           data={"account_id": acct_a, "mapping": json.dumps(mapping)}, headers=C)
check("a near-duplicate is imported but flagged a suspect",
      r.status_code == 200 and r.json().get("suspects", 0) >= 1, f"{body(r)}")

# ---------------------------------------------------------------- reports
section("reports (incl. owner filter and trend)")
r = c.get("/reports/net-worth", params={"start": day(-30), "end": day(0)})
check("net worth", r.status_code == 200, f"{r.status_code} {body(r, 200)}")
base_nw = r.json() if r.status_code == 200 else {}
check("net worth declares its attribution", base_nw.get("attribution") == "account",
      f"attribution={base_nw.get('attribution')!r}")

r = c.get("/reports/net-worth", params={"start": day(-30), "end": day(0), "owner_id": alice})
check("net worth accepts an owner filter", r.status_code == 200, f"{r.status_code} {body(r, 200)}")

for path in ("/reports/cash-flow", "/reports/spending"):
    r = c.get(path, params={"start": day(-30), "end": day(0), "owner_id": alice})
    check(f"{path} with owner filter", r.status_code == 200, f"{r.status_code} {body(r, 200)}")

# ---------------------------------------------------------------- signup joins the household
section("open signup joins the existing household")
fresh = httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True)
email = f"second-{RUN}@example.com"
r = fresh.post("/auth/signup", json={
    "email": email, "display_name": f"Second {RUN}", "password": "secondpassword123"})
check("signup succeeds", r.status_code == 201, f"{r.status_code} {body(r)}")
if r.status_code == 201:
    second = r.json()
    check("second signup joins the SAME household", second["household_id"] == me["household_id"],
          f"{second['household_id']} vs {me['household_id']}")
    check("second signup is a plain member, not an owner", second["role"] == "member",
          f"role={second['role']}")
    r = fresh.get("/owners")
    check("the new member sees the shared household owners", r.status_code == 200, f"{r.status_code}")

    # Reads are open to any member; every owner write is owner-only, so a member
    # cannot create an owner it has no way to remove.
    member_headers = {"X-CSRF-Token": second["csrf_token"]}
    r = fresh.post("/owners", json={"name": f"nope {RUN}"}, headers=member_headers)
    check("a member cannot create owners", r.status_code == 403, f"{r.status_code}")
    r = fresh.patch(f"/owners/{alice}", json={"name": f"hijack {RUN}"}, headers=member_headers)
    check("a member cannot rename owners", r.status_code == 403, f"{r.status_code}")
    r = fresh.delete(f"/owners/{alice}", headers=member_headers)
    check("a member cannot delete owners", r.status_code == 403, f"{r.status_code}")

    r = c.get("/household/members")
    emails = [m.get("email") for m in r.json()] if r.status_code == 200 else []
    check("the new member appears on the roster", email in emails, f"{emails}")

print(f"\n{'=' * 60}\n{ok} passed, {bad} failed   (run id {RUN})\n{'=' * 60}")
sys.exit(1 if bad else 0)
