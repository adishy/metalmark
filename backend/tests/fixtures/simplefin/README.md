# SimpleFIN fixtures

ADR-0022 requires that the sync fixtures be **derived from a real capture of the
public demo bridge**, not invented — hand-authored fixtures cannot tell you that
your assumptions about the provider's wire format are wrong, which is the entire
point of running the spike.

## Provenance

| File | Status |
|---|---|
| `demo_capture.json` | **Captured**, verbatim. Trimmed only in *volume* (6 transactions per account, one holding), never in shape. |

Everything else the tests need is **constructed** in `backend/tests/fakes/simplefin.py`
by transforming the captured payload via the *production* parser
(`app.services.simplefin.parse_accounts_payload`). Each constructed scenario carries
a comment naming the assumption it encodes. The distinction matters: the capture
proves the wire format, and the constructions encode our *behavioural* assumptions
about it.

Routing the constructions through the production parser is deliberate. A fake that
built its DTOs by hand would agree with itself and prove nothing about the wire
format; going through the real parser means the capture genuinely validates our
reading of it.

Three things the capture cannot show, so every test for them is constructed:

| Not in the capture | Why |
|---|---|
| Pending transactions | The demo never emits `posted: 0`. |
| Reconnects with changed ids | The demo's account ids are *stable* across a re-claim. |
| Auth failures (`con.auth`) | The demo bridge will not produce a revoked credential on request. |

## How the capture was taken

```bash
# 1. The demo setup token is minted per page load — fetch a fresh one.
TOKEN=$(curl -s https://beta-bridge.simplefin.org/info/developers \
        | grep -oE '[A-Za-z0-9+/]{60,}={0,2}' | head -1)

# 2. base64-decode it to a claim URL, POST with an empty body; the response is
#    the access URL (plain text, Basic-auth credentials embedded).
CLAIM_URL=$(printf '%s' "$TOKEN" | base64 -d)
curl -s -X POST "$CLAIM_URL" -o access_url.txt      # never echo this file

# 3. Fetch. Note `version=2`, which is what adds payee/memo/mcc.
curl -s "$(cat access_url.txt)/accounts?version=2&pending=1&start-date=$(date -u -v-45d +%s)"
```

The access URL is a credential and is **not** committed; only the `/accounts`
response body is. The demo data is synthetic by construction (three sample
accounts, "SimpleFIN Savings" / "SimpleFIN Checking" / "SimpleFIN Empty Account").

## What the capture corrected

Six things the written protocol docs get wrong or leave out. Each one changed the
implementation, so they are recorded here rather than rediscovered later.

1. **`/simplefin/info` advertises `{"versions": ["1.0"]}`**, not `2` — yet
   `version=2` is accepted and is what adds the richer fields. Do not gate on the
   advertised list.

2. **Transactions *do* carry `payee`, `memo` and `mcc`** (under `version=2`). The
   published spec's field list omits them, and an earlier reading of it concluded
   they did not exist. `payee` is a clean merchant name (`"John's Fishin Shack"`)
   where `description` is a category-ish label (`"Fishing bait"`) and `memo` is the
   raw bank string (`"JOHNS FISHIN SHACK BAIT"`). **This is why sync sets
   `merchant` from `payee`** instead of leaving it for a rule: the provider is
   handing us a structured merchant and discarding it would be worse. A rule may
   still improve it, which is how the "a rule may improve a provider value"
   acceptance bar is exercised.

3. **There is no `pending` field on a transaction.** Pending must be derived from
   `posted == 0`, exactly as the spec's prose says and its field list implies
   otherwise.

4. **Accounts carry no `type` or `subtype`.** Type inference is name-based, plus
   "has non-empty `holdings`" for the investment case. The guess is always
   correctable by hand through `PATCH /accounts/{id}`.

5. **Transaction ids are unique *within* an account but repeat *across* accounts** —
   the capture has all 170 ids shared between Savings and Checking. The demo
   derives `id` from the posting epoch second, but the lesson generalises: **never
   look a transaction up by `external_id` alone.** Always scope to `account_id`,
   which is what the `(account_id, external_id)` unique index already enforces.
   `mcc` is also nullable despite being present on every row (6 of 170 are null).

6. **`errlist` entries are objects, not strings** — `{"code": "...", "msg": "..."}`.
   An earlier reading assumed a flat list of strings; a parser written to that
   assumption would have put a Python dict's `repr` into the run log. Captured
   verbatim, on HTTP 200:

   ```json
   {"code": "gen.api", "msg": "Requested date range exceeds limit of 90 days and was capped."}
   ```

   Only the two `gen.api` window warnings have been observed. `con.auth` and
   `act.*` are documented codes that the demo bridge will not produce, so their
   message wording is unverified — route on the **code prefix**, never on text.

7. **The demo regenerates its data relative to *now*, so transaction ids and dates
   shift on every fetch.** Two fetches moments apart returned the same amounts,
   descriptions and payees, one day later, with every id re-minted. This is the
   id-instability case ADR-0022 said only a real capture could reveal — and it
   means **a connection pointed at the live demo bridge will accumulate
   duplicates**, because the ids genuinely change. It is a property of the demo,
   not of SimpleFIN, and it is precisely why CI must sync against the frozen
   fixtures through `FakeProvider` rather than against the live bridge.

## Request window: 44 days, not 90

The bridge answers a long `start-date` with **HTTP 200 and an `errlist` entry** —
it caps the range rather than failing:

- `90 days or more` → `gen.api: "Requested date range exceeds limit of 90 days and was capped."`
- `45 days or more` → `gen.api: "Requested date range exceeds recommended range of 45 days. In the future, this may be capped."`

So the first-sync lookback is **44 days**, not 90: asking for more returns correct
data plus a warning on every single first sync. And because these arrive on a 200,
`gen.api` must map to a run status of **`partial`** — a warning about *our* request
— never to `connections.status`, which is about the connection's health.

### Why 44 and not 45 — a correction to this file

This section originally said `> 45 days`, and both thresholds were read the same
way: off a *single* capture whose request happened to be `now − 45d`. That capture
warned, and the rule was inferred as "more than 45". The inference was one day
wrong, and the error was not free — the first-sync lookback was then set to 45,
which is the value that *trips* the warning rather than the last value that does
not. Every real connection's first sync reported `partial` for a range the bridge
had told us was fine.

Re-measured against the live bridge — `scripts/probe_live_bridge.py` does it, one
fresh demo token per request, `start-date` at a fixed offset from now:

| asked for | recommended warning | capped |
|---|---|---|
| 44d, 44d+1h, 44d+2h | no | no |
| 44d+3h, 44d+23h, 45d−60s, 45d | **yes** | no |
| 89d | **yes** | no |
| 90d, 91d, 365d | no | **yes** |

Two things fall out of that table, and both are one-off-by-one corrections to the
paragraphs above.

**The recommended boundary falls on the calendar date** of `start-date`, not on the
elapsed duration: the first three rows land on a date 44 days back and the next
four on one 45 days back, whatever the time of day. So the trigger is "the start
date is 45 or more days ago", and the largest safe window is 44 days.

**The cap sits at exactly 90, not above it** — and it *replaces* the recommended
warning rather than joining it: a request for 90 days or more returns one `gen.api`
entry, about the cap. The outcome is the same (a `gen.api` maps to `partial`
either way), but a parser that reported "both complaints" would be reporting
something the bridge does not send.

**The lesson the capture could not teach:** a threshold read off one sample is a
threshold plus or minus the step you happened to sample at. The capture's *shapes*
— correction 6's `errlist` objects, correction 2's `payee` — are trustworthy
because they are verbatim data. Its *boundaries* are not, and this is the one place
the difference bit. `tests/unit/test_sync.py` now pins
`FIRST_SYNC_WINDOW_DAYS < RECOMMENDED_WINDOW_DAYS`, which is the invariant that
would have caught it.
