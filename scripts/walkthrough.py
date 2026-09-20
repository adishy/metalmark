"""End-to-end walkthrough of the M1a paths the Playwright suite does not reach.

Runs against a live stack over real HTTP, as the dev owner, and prints a PASS/FAIL
line per assertion. Everything it creates is tagged with a run id, so a failed run
is identifiable in the UI afterwards.

It complements the other suites rather than repeating them. Playwright covers the
paths a *user* clicks; this covers the ones that are easier to state precisely as
HTTP — the ownership chain and its filters, the split-sum invariant, what a
transfer link does to cash flow and revaluation, rule provenance and idempotency,
CSV re-import and near-duplicates, the signup/household rules, and the bank-sync
vertical end to end. The pytest suite covers the same services in-process against
a throwaway database; this is the check that the same things are true of the
assembled app.

The bank-sync section is the one that can SKIP rather than pass: it needs a stack
started with ``METALMARK_SIMPLEFIN_PROVIDER=fake``, which is a launch-time
property no request can change. It says so, loudly, and the footer counts it.

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
import time
import uuid

import httpx

BASE = os.environ.get("WALKTHROUGH_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("WALKTHROUGH_EMAIL", "owner@example.com")
PASSWORD = os.environ.get("WALKTHROUGH_PASSWORD", "devpassword123")
RUN = uuid.uuid4().hex[:6]
ok = bad = skipped = 0


def check(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  PASS  {name}  {detail}")
    else:
        bad += 1
        print(f"  FAIL  {name}  {detail}")
    return cond


def skip(name, detail=""):
    """A check that did not run, because this stack cannot answer it.

    Not a silent `return`: a gate that asserted nothing must not read as a gate
    that passed everything, so it prints where a PASS would have, and the footer
    counts it. There is exactly one user of this — see the bank-sync section —
    and the condition is a property of how the stack was *started*, which no
    request can change.
    """
    global skipped
    skipped += 1
    print(f"  SKIP  {name}  {detail}")


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
    row = next((m for m in r.json()["points"] if m["month"] == MONTH), None)
    return float(row["income"] if income else row["expense"]) if row else 0.0


def net_worth_parts():
    """The whole reconciliation, not just the terms that happened to add up."""
    r = c.get("/reports/net-worth", params={"start": day(-40), "end": day(1)})
    if r.status_code != 200:
        return None
    return r.json()


def terms(d):
    """Every term of the identity, as numbers, for printing and arithmetic."""
    return (float(d["delta_net_worth"]), float(d["net_cash_flow"]),
            float(d["currency_revaluation"]), float(d["market_appreciation"]),
            float(d["unexplained"]))


def term_line(d):
    delta, cf, reval, appr, unexpl = terms(d)
    return (f"ΔNW={delta} CF={cf} reval={reval} appr={appr} unexplained={unexpl}")


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
    d0, cf0, reval0, appr0, unexpl0 = terms(nw_before)

    # ΔNW = cash flow + currency revaluation + market appreciation + unexplained
    # (ADR-0032). Only the first four are read off the ledger; `unexplained` is the
    # remainder, so this one check would pass even if a term were silently missing —
    # it is here to pin the *shape* of the payload and to prove the figures survive
    # the round trip as exact decimals. The two checks under it are the ones that
    # can actually fail.
    check("the four terms reconcile to the change in net worth",
          abs(d0 - (cf0 + reval0 + appr0 + unexpl0)) < 0.01,
          term_line(nw_before))

    # A residual with no name is not a finding, so a material one must arrive with
    # the accounts behind it: largest first, each of them material to the residual
    # (the service drops anything under 1% of it — a list of every account the
    # household ever had is noise wearing a finding's clothes).
    named = nw_before["unexplained_by_account"]
    material = max(1.0, abs(unexpl0) * 0.01)
    sizes = [abs(float(row["amount"])) for row in named]
    check("a material unexplained change is attributed to named accounts",
          abs(unexpl0) < 0.05 or len(named) > 0,
          f"unexplained={unexpl0}, {len(named)} named")
    check("attribution is material-only, largest first",
          all(row["name"] for row in named)
          and sizes == sorted(sizes, reverse=True)
          and all(size >= material - 0.0001 for size in sizes),
          f"materiality={material:.2f} sizes={sizes}")

    # The tautology this replaced: if revaluation were computed as ΔNW − CF, then
    # every residual would be invisible by construction — which is exactly how the
    # conversion cost stayed hidden until it broke the sync e2e. Revaluation is a
    # term in its own right, so where there is a residual it is not simply the plug.
    if abs(unexpl0) > 0.05:
        check("revaluation is a computed term, not the leftover",
              abs(reval0 - (d0 - cf0)) > 0.05,
              f"reval={reval0} vs ΔNW−CF={d0 - cf0}")

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
        da, cfa, revala, appra, unexpla = terms(nw_after)
        # No money left the household, so net worth is unmoved by the *link* — but
        # the conversion cost it reveals must still be accounted for somewhere, and
        # it is: it moves out of cash flow and into revaluation.
        check("linking does not change net worth",
              abs(da - d0) < 0.01,
              f"ΔNW {d0} -> {da}")
        # Unlinked, the two legs leave a hole in cash flow (you sent 100 and booked
        # 90 * 0.925 back). Linked, they leave cash flow entirely and the same hole
        # reappears as revaluation — the conversion cost, no longer disguised as
        # ordinary spending.
        check("the conversion cost moves from cash flow into revaluation",
              abs((cfa - cf0) - (out_base - in_base)) < 0.01
              and abs((revala - reval0) + (out_base - in_base)) < 0.01,
              f"CF {cf0} -> {cfa}, reval {reval0} -> {revala}, legs {out_base}/{in_base}")
        # And it lands in a *named* term rather than in the remainder. This is the
        # check the tautology used to hide: with revaluation computed as ΔNW − CF,
        # the residual could not move, because the hole was being poured into the
        # term next to it. Both directions are asserted, so a conversion cost that
        # vanished (or landed twice) fails here rather than passing quietly.
        check("the conversion cost does not move the residual",
              abs(unexpla - unexpl0) < 0.01,
              f"unexplained {unexpl0} -> {unexpla}; {term_line(nw_after)}")

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
    # Both scope rows, unlike net worth above, and the payload now says so — otherwise
    # a client has no way to know these two do not add up with the account-scoped one.
    reported = r.json().get("attribution") if r.status_code == 200 else None
    check(f"{path} declares row attribution", reported == "row", f"attribution={reported!r}")

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

# ---------------------------------------------------------------- bank sync
section("bank sync: claim, run, and the idempotency bar")


def runs_for(connection_id):
    r = c.get("/connections/runs", params={"connection_id": connection_id, "limit": 10})
    return r.json() if r.status_code == 200 else []


def wait_for_run(connection_id, previous=None, deadline_s=90.0):
    """The newest **settled** run for a connection, once it is not `previous`.

    A run is ``ok|partial|error|cancelled|running`` and only ``running`` means
    "not yet". Polled rather than slept on, because the work happens in another
    container and the queue is drained on the worker's own tick — nothing this
    process does is observable as an event, so there is nothing to wait *on*.
    """
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        runs = runs_for(connection_id)
        if runs and runs[0]["id"] != previous and runs[0]["status"] != "running":
            return runs[0]
        time.sleep(1.0)
    return None


# M1a was manual. This is the M2 vertical over real HTTP: claim a bank, watch a
# run appear, read its log back, and prove a second sync writes nothing.
# Playwright walks the same path through the UI (`e2e/sync.spec.ts`); what this
# adds is the assertions that are about *values* rather than about what a screen
# shows — the counters, the event trail, and the refusals the API promises.
#
# It needs the stack started with the fake provider, which is a property of how
# the process was launched and not something a request can ask for:
#
#     METALMARK_SIMPLEFIN_PROVIDER=fake docker compose up -d --build
#
# Without it the claim is handed to the live bridge, which correctly refuses a
# token that is not one — a right answer to a question this file did not mean to
# ask, so it SKIPs and names the command rather than failing. CI exports the
# variable for the whole job, so CI asserts.
r = c.post("/connections/claim", json={"setup_token": f"walkthrough-{RUN}"}, headers=C)
if r.status_code != 201 or (r.json().get("provider") != "fake"):
    skip("bank sync vertical",
         f"claim -> {r.status_code} {body(r, 120)}. Start the stack with "
         f"METALMARK_SIMPLEFIN_PROVIDER=fake to assert it.")
else:
    conn = r.json()
    cid = conn["id"]
    check("a claimed bank is healthy and unpaused",
          conn["status"] == "ok" and conn["is_enabled"] is True,
          f"status={conn['status']} enabled={conn['is_enabled']} provider={conn['provider']}")

    # The claim response is the one place a credential is in scope, so it is the
    # one worth asserting structurally. `access_url_encrypted` is the column that
    # holds the ciphertext of a Basic-auth URL (`schemas/connections.py`), and the
    # rule there is that the field does not *exist* on the way out rather than
    # that it is nulled — a key named here would be a key somebody fills in later.
    check("the claim response carries no credential",
          not any("access" in k or "token" in k for k in conn), f"keys={sorted(conn)}")

    # The cadence bounds are the API's, not the select's: the panel can only offer
    # what its list contains, so this is where they are exercised as refusals.
    r = c.patch(f"/connections/{cid}", json={"sync_interval_minutes": 30}, headers=C)
    check("below the cadence floor is refused", r.status_code == 422, f"{r.status_code}")
    r = c.patch(f"/connections/{cid}", json={"sync_interval_minutes": 20160}, headers=C)
    check("above the cadence ceiling is refused", r.status_code == 422, f"{r.status_code}")

    # Park the cadence at its ceiling *before* syncing by hand. A claim sets
    # `next_sync_at` to now, so the cron is otherwise entitled to enqueue a run of
    # its own partway through this section, and the "newest run" read back below
    # would then be the cron's rather than the one this triggered.
    r = c.patch(f"/connections/{cid}", json={"sync_interval_minutes": 10080}, headers=C)
    check("the interval round-trips",
          r.status_code == 200 and r.json().get("sync_interval_minutes") == 10080,
          f"{r.status_code} {body(r, 120)}")

    r = c.post(f"/connections/{cid}/sync", headers=C)
    # 202, not 200: nothing has synced yet, and a synchronous response would block
    # the request for the length of a bank fetch. The queue exists so that it does
    # not, so the status code is the assertion that the queue is in the path.
    check("sync-now queues a job", r.status_code == 202, f"{r.status_code} {body(r, 120)}")
    job = r.json() if r.status_code == 202 else {}

    first = wait_for_run(cid)
    if check("the worker ran it and the run is readable", first is not None,
             f"run={first['id'][:8]} status={first['status']}" if first else
             "no settled run within 90s — is the worker container up?"):
        check("the run succeeded", first["status"] == "ok",
              f"status={first['status']} error={first.get('error')}")
        # The fetch's own telemetry is `—` here, and that is the assertion rather
        # than a gap in it: `FetchStats` is filled by `SimpleFinProvider` from the
        # response it read, and the fake makes no request, so it reports none of it
        # (`http_status` is the *failure* code the panel chips beside a run, so a
        # run with nothing to complain about has nothing to put there either).
        # Asserting 200 would assert that the fake went to the network — the one
        # thing it exists not to do. The success path is pinned against a scripted
        # response in `tests/integration/test_sync.py`, which needs no bank.
        check("a scripted provider records no fetch telemetry",
              first["http_status"] is None and first["http_ms"] is None
              and first["bytes_fetched"] is None,
              f"http_status={first['http_status']} http_ms={first['http_ms']} "
              f"bytes={first['bytes_fetched']}")
        # Deliberately not "inserted > 0". The ledger is shared, so on a database
        # where another connection already synced this capture the correct count
        # is zero — ADR-0009 keys an account on institution + name, not on the
        # connection, so a second connection reporting the same accounts is a
        # reconnect. What must hold everywhere is that every account the provider
        # reported was created or matched, which is what this asserts; the insert
        # guarantee that is environment-independent is the second run below.
        check("the payload resolved into accounts",
              first["accounts_seen"] > 0
              and first["accounts_created"] + first["accounts_remapped"] > 0,
              f"seen={first['accounts_seen']} created={first['accounts_created']} "
              f"remapped={first['accounts_remapped']}")

        d = c.get(f"/connections/runs/{first['id']}")
        detail = d.json() if d.status_code == 200 else {}
        events = detail.get("events", [])
        names = [e["event"] for e in events]
        seqs = [e["seq"] for e in events]
        # `window.computed` is the run's first statement of fact — the window it
        # decided to ask the bridge for, which is the thing a wrong run gets wrong
        # and the reason it is logged before the fetch rather than after.
        check("the run carries a log from window.computed to run.finished",
              d.status_code == 200 and names[:1] == ["window.computed"] and "run.finished" in names,
              f"{d.status_code} {names}")
        # `seq` exists because `ts` cannot order this: Postgres `now()` is the
        # *transaction* timestamp, so every event in one ingest shares it exactly
        # (`services/connections.py::list_run_events`). Monotonic and unique is
        # the whole contract this order is built on.
        check("the log's order is total", seqs == sorted(seqs) and len(set(seqs)) == len(seqs),
              f"seq={seqs[:12]}")
        # The token this walked in with is the one secret in scope. It is not the
        # real credential — that never reaches this process — but a leak is a
        # leak, and this is the run's own log and the connection row: the two
        # places ADR-0016 names.
        blob = json.dumps(detail) + json.dumps(c.get(f"/connections").json())
        check("no trace of the setup token in the run log or the connection list",
              f"walkthrough-{RUN}" not in blob, f"{len(blob)} bytes searched")

        # The headline property of the whole workstream: syncing the same data
        # again inserts nothing and updates nothing. A re-run that reports work is
        # either duplicating history or rewriting fields a human owns.
        r = c.post(f"/connections/{cid}/sync", headers=C)
        check("the second sync queues too", r.status_code == 202, f"{r.status_code}")
        second = wait_for_run(cid, previous=first["id"])
        if check("the second run lands", second is not None,
                 f"run={second['id'][:8]} status={second['status']}" if second else
                 "no second run within 90s"):
            check("re-syncing the same data writes nothing",
                  second["txns_inserted"] == 0 and second["txns_updated"] == 0,
                  f"inserted={second['txns_inserted']} updated={second['txns_updated']} "
                  f"reconciled={second['txns_reconciled']}")

    # Cancelling is a race — the job this targets has certainly finished by now —
    # and the API promises one of two answers rather than a fixed one, so this
    # asserts the *refusal* half. The other half (a job that is genuinely still
    # queued disappears) needs a job held open, which is what the panel's e2e
    # covers and what a sync that finishes in milliseconds cannot be made to do.
    if job:
        r = c.post(f"/connections/jobs/{job['id']}/cancel", headers=C)
        check("cancelling a finished job is refused, not silently accepted",
              r.status_code == 409 and "already finished" in body(r, 200).lower(),
              f"{r.status_code} {body(r, 120)}")

summary = f"{ok} passed, {bad} failed"
if skipped:
    summary += f", {skipped} skipped"
print(f"\n{'=' * 60}\n{summary}   (run id {RUN})\n{'=' * 60}")
sys.exit(1 if bad else 0)
