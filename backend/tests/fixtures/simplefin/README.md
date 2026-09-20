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

## Request window: 45 days, not 90

The bridge answers a long `start-date` with **HTTP 200 and an `errlist` entry** —
it caps the range rather than failing:

- `> 90 days` → `gen.api: "Requested date range exceeds limit of 90 days and was capped."`
- `> 45 days` → `gen.api: "Requested date range exceeds recommended range of 45 days. In the future, this may be capped."`

So the first-sync lookback is **45 days**, not 90: asking for more returns correct
data plus a warning on every single first sync. And because these arrive on a 200,
`gen.api` must map to a run status of **`partial`** — a warning about *our* request
— never to `connections.status`, which is about the connection's health.
