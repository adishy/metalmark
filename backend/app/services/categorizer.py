"""The auto-categorizer: a likely category for a transaction, worked out here.

**Private by construction.** Nothing leaves the process: no model, no web lookup,
no merchant database. The inputs are the row's own text and amount, the
household's categories, and what the household has already categorized by hand.

Three signals, strongest first:

1. **The household's own history.** A merchant a human has filed before is filed
   the same way — the most common choice wins, and it has to be a clear majority.
   This is what makes the categorizer aware of *custom* categories: once a person
   has filed "BLUE BOTTLE" under their own "Coffee Beans", it follows.
2. **Keywords** for the starter categories (``default_categories``), matched on
   merchant and description. A custom category also matches on its own name —
   whole words of four letters or more, and only when no keyword matched.
3. **Linked transfers** are filed as a transfer — a payment to a card as a Credit
   Card Payment — because the link already says what they are.

**The sign of the amount is a constraint.** Money in goes to an income or a
transfer category, money out to an expense or a transfer category — so a refund
from a shop is never "Paychecks", and a paycheck is never "Clothing". History is
the exception: a household that files its shop refunds under "Clothing" is
followed, because it said so.

**Provenance (ADR-0007).** What this writes is marked ``auto``: below a human
and below a rule, so either can correct it and a rule re-run can improve it. The
automatic path (sync) fills only a *blank* category; ``categorize_all`` is the
admin's explicit "overwrite everything", and says so before it runs.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Account, Category, CategoryGroup, Transaction
from app.services import transactions as txn_service

AUTO = "auto"
USER = "user"
RULE = "rule"

# Keywords per starter category, as regexes over "merchant description",
# lower-cased. Whole-word where a short word would otherwise match inside
# another ("gas" in "vegas"). Order inside the table is priority: the first
# category whose pattern matches wins, so the specific sit above the general.
_KEYWORDS: list[tuple[str, str]] = [
    # Transfers first: a transfer described with a shop's word is still a transfer.
    ("Credit Card Payment", r"(payment\s*-?\s*thank\s*you|autopay|card\s*payment|"
                            r"crd\s*pmt|epay|credit\s*card\s*pmt|applecard\s*gsbank|"
                            r"payment\s*to\s*(chase|amex|citi|discover|capital\s*one))"),
    ("Loan Repayment", r"\b(loan\s*(payment|pmt|repay)|student\s*loan|navient|nelnet|"
                       r"sallie\s*mae|mohela|aidvantage|mortgage\s*pmt)\b"),
    ("Transfer", r"(\btransfer\b|\bxfer\b|\btrnsfr\b|online\s*banking\s*to|"
                 r"from\s*(savings|checking)|to\s*(savings|checking)|\bach\s*(debit|credit)?"
                 r"\s*(fidelity|schwab|vanguard|robinhood|wealthfront|betterment|etrade|"
                 r"e\*trade|m1\s*finance|coinbase)|\b(fidelity|schwab|vanguard|robinhood|"
                 r"wealthfront|betterment|coinbase)\b.*\b(moneyline|brokerage|invest|"
                 r"contrib|deposit|funding)|wire\s*(in|out|transfer)|brokerage\s*link)"),
    # Income.
    ("Paychecks", r"(payroll|\bpayrl\b|direct\s*dep|\bdir\s*dep|salary|paycheck|"
                  r"\bgusto\b|\badp\b|paychex|workday|\bwages?\b)"),
    ("Tax Refund", r"(tax\s*ref|irs\s*treas|us\s*treasury\s*310|state\s*tax\s*refund|"
                   r"\bftb\b.*refund)"),
    ("Interest", r"(\binterest\b|\bint\s*pd\b|\bint\s*earned\b|\bdividend\b|\bdiv\b)"),
    ("Owed to Me", r"(venmo\s*cashout|reimburse|\brefund\s*from\b)"),
    # Housing.
    ("Rent", r"(\brent\b|apartment|apts?\b|property\s*mgmt|leasing|\bavalon\b|"
             r"equity\s*residential|greystar|essex\s*prop|zillow\s*rent|bilt\s*rent)"),
    ("Insurance – Renter's", r"(lemonade|renters?\s*ins)"),
    ("Home Improvement", r"(home\s*depot|lowe'?s|ace\s*hardware|true\s*value|menards|"
                         r"hardware)"),
    ("Furniture & Housewares", r"(ikea|wayfair|west\s*elm|crate\s*&?\s*barrel|"
                               r"pottery\s*barn|bed\s*bath|container\s*store|homegoods)"),
    ("Kitchen", r"(williams[-\s]*sonoma|sur\s*la\s*table|kitchen)"),
    # Bills.
    ("Bills – Communication", r"(verizon|at&t|\batt\b|t-?mobile|comcast|xfinity|spectrum|"
                              r"mint\s*mobile|google\s*fi|visible|cricket|sonic\.net|"
                              r"\bphone\b|internet|wireless)"),
    ("Utilities", r"(pg&e|\bpge\b|con\s*ed|edison|duke\s*energy|electric|\bgas\s*co\b|"
                  r"water\s*(dept|district|util)|utilit|power\s*co|national\s*grid)"),
    # Subscriptions (before Entertainment/Shopping, which would claim "apple").
    ("Subscription – News", r"(nytimes|new\s*york\s*times|\bwsj\b|wall\s*street\s*j|"
                            r"washington\s*post|economist|the\s*atlantic|substack|"
                            r"bloomberg|financial\s*times|\bft\.com)"),
    ("Subscription – Privacy", r"(mullvad|proton|nordvpn|expressvpn|1password|bitwarden|"
                               r"lastpass|dashlane|\bvpn\b|fastmail|tutanota|simplelogin|"
                               r"deleteme|privacy\.com)"),
    ("Subscription – Productivity", r"(notion|todoist|evernote|dropbox|microsoft\s*365|"
                                    r"office\s*365|google\s*(one|workspace)|adobe|"
                                    r"grammarly|calendly|zoom\.us|\bzoom\b|obsidian|"
                                    r"readwise|superhuman)"),
    ("Subscription – Dev / SaaS", r"(github|gitlab|openai|anthropic|claude\.ai|chatgpt|"
                                  r"vercel|netlify|heroku|digitalocean|linode|hetzner|"
                                  r"aws|amazon\s*web\s*serv|google\s*cloud|gcp|"
                                  r"cloudflare|fly\.io|render\.com|jetbrains|cursor|"
                                  r"sentry|datadog|twilio|ngrok|tailscale)"),
    ("Domain Registration", r"(namecheap|porkbun|godaddy|hover\.com|gandi|"
                            r"domain|squarespace\s*domains)"),
    ("Storage", r"(icloud|backblaze|\bb2\b|wasabi|sync\.com|pcloud|storage)"),
    ("Subscription – Entertainment", r"(netflix|hulu|disney\s*\+|disney\s*plus|hbo|"
                                     r"\bmax\.com|spotify|apple\s*music|youtube\s*(premium|"
                                     r"music|tv)|paramount|peacock|crunchyroll|audible|"
                                     r"sirius|tidal|apple\.com/bill)"),
    ("Video Games", r"(steam|steampowered|valve|nintendo|playstation|psn|xbox|"
                    r"epic\s*games|gog\.com|humble\s*bundle|itch\.io|blizzard|riot\s*games)"),
    ("Creator Support", r"(patreon|ko-?fi|buy\s*me\s*a\s*coffee|onlyfans|twitch|"
                        r"github\s*sponsors|memberful)"),
    ("Physical & Digital Media", r"(kindle|google\s*play|itunes|bandcamp|vinyl|"
                                 r"amoeba|records)"),
    ("Movie Theater", r"(amc\s*theat|regal|cinemark|alamo\s*drafthouse|cinema|theatre|"
                      r"theater|fandango)"),
    ("Events", r"(ticketmaster|stubhub|eventbrite|seatgeek|livenation|axs\.com|"
               r"\btickets?\b|museum|concert)"),
    # Food.
    ("Groceries", r"(safeway|trader\s*joe|whole\s*foods|wholefds|kroger|costco|"
                  r"sprouts|aldi|lidl|publix|wegmans|h-?e-?b\b|ralphs|vons|albertsons|"
                  r"berkeley\s*bowl|mollie\s*stone|grocery|grocer|market|"
                  r"instacart|99\s*ranch|h\s*mart|patel\s*brothers|food\s*4\s*less|"
                  r"smart\s*&\s*final|winco|meijer|giant\s*eagle|stop\s*&\s*shop|"
                  r"food\s*lion|harris\s*teeter|hy-?vee)"),
    ("Bars & Coffee Shops", r"(starbucks|peet'?s|blue\s*bottle|philz|dunkin|"
                            r"coffee|cafe|café|espresso|tea\s*house|boba|"
                            r"\bbar\b|brewing|brewery|taproom|pub\b|tavern|saloon|lounge)"),
    ("Restaurants", r"(doordash|uber\s*eats|ubereats|grubhub|postmates|caviar|"
                    r"restaurant|grill|kitchen\s*&|pizza|pizzeria|taqueria|sushi|ramen|"
                    r"burger|chipotle|mcdonald|wendy'?s|taco\s*bell|subway|panera|"
                    r"sweetgreen|cava\b|shake\s*shack|in-?n-?out|chick-?fil|"
                    r"panda\s*express|thai|pho\b|diner|bistro|bbq|\btst\*|\bsq\s*\*.*"
                    r"(cafe|kitchen|grill)|eatery|deli\b|bakery|dim\s*sum|curry)"),
    # Transport.
    ("Gas", r"(\bshell\b|chevron|exxon|mobil\b|\bbp\b|arco|valero|sunoco|"
            r"76\s*gas|phillips\s*66|marathon\s*petro|speedway|circle\s*k|"
            r"gas\s*station|fuel|\bgas\b)"),
    ("Parking & Tolls", r"(parking|parkmobile|spothero|paybyphone|fastrak|e-?zpass|"
                        r"sunpass|\btoll|garage)"),
    ("Insurance – Auto", r"(geico|progressive|state\s*farm|allstate|farmers\s*ins|"
                         r"liberty\s*mutual|usaa\s*(p&c|ins)|auto\s*ins)"),
    ("Auto Maintenance", r"(jiffy\s*lube|midas|pep\s*boys|firestone|goodyear|"
                         r"discount\s*tire|les\s*schwab|autozone|o'?reilly\s*auto|"
                         r"napa\s*auto|car\s*wash|oil\s*change|auto\s*repair|smog)"),
    ("Car Rental", r"(hertz|avis|enterprise\s*rent|budget\s*rent|national\s*car|"
                   r"sixt|turo|zipcar|getaround|alamo\s*rent)"),
    ("Car Payments", r"(toyota\s*financial|honda\s*financial|ally\s*auto|"
                     r"tesla\s*(finance|lease)|auto\s*loan|car\s*payment)"),
    ("Car – Other", r"(\bdmv\b|registration\s*fee|tesla\s*supercharg|chargepoint|"
                    r"electrify\s*america|evgo)"),
    ("Public Transit", r"(clipper|bart\b|\bmta\b|metro\s*card|metrocard|caltrain|"
                       r"amtrak|muni\b|septa|wmata|mbta|cta\s*ventra|ventra|"
                       r"transit|\bomny\b|go\s*pass|njt|lirr|metrolink)"),
    ("Taxi & Ride Shares", r"(\buber\b(?!\s*eats)|\blyft\b|waymo|taxi|\bcab\b|curb\s*mobility)"),
    ("Bicycle", r"(bike|bicycle|cycles|rei\s*co-?op\s*cycle|lime\b|bird\s*rides|citi\s*bike|"
                r"bay\s*wheels)"),
    # Travel.
    ("Travel & Vacation", r"(airbnb|vrbo|expedia|booking\.com|hotels?\b|marriott|hilton|"
                          r"hyatt|ihg|airline|airways|united\s*air|delta\s*air|american\s*air|"
                          r"southwest|jetblue|alaska\s*air|frontier|spirit\s*air|"
                          r"air\s*india|emirates|lufthansa|british\s*airways|kayak|"
                          r"priceline|hostel|resort|tsa\s*pre|global\s*entry)"),
    # Health.
    ("Dentist", r"(dental|dentist|orthodont|dds\b)"),
    ("Medical Equipment", r"(medical\s*supply|cpap|hearing\s*aid|eyeglass|warby\s*parker|"
                          r"contacts|1-800\s*contacts|zenni)"),
    ("Medical", r"(cvs|walgreens|rite\s*aid|pharmacy|hospital|clinic|medical|"
                r"kaiser|sutter|one\s*medical|labcorp|quest\s*diag|urgent\s*care|"
                r"\bmd\b|physician|health\s*center|optometr|copay)"),
    ("Fitness", r"(\bgym\b|fitness|24\s*hour|planet\s*fitness|equinox|crossfit|"
                r"yoga|pilates|climbing|bouldering|orangetheory|peloton|strava|"
                r"classpass|\bymca\b)"),
    ("Haircut", r"(barber|salon|haircut|great\s*clips|supercuts|sport\s*clips|hair)"),
    # Education.
    ("Education – Courses", r"(coursera|udemy|edx|udacity|skillshare|masterclass|"
                            r"tuition|university|college|school|course|bootcamp|"
                            r"duolingo|brilliant\.org|frontend\s*masters|pluralsight)"),
    ("Education – Study Material", r"(chegg|pearson|mcgraw|textbook|o'?reilly\s*media|"
                                   r"manning|leetcode|kaplan|magoosh|princeton\s*review)"),
    # Shopping.
    ("Books", r"(barnes\s*&?\s*noble|bookshop|books?\b|powell'?s|half\s*price\s*books|"
              r"abebooks|thriftbooks)"),
    ("Stationery", r"(staples|office\s*depot|officemax|paper\s*source|muji|jetpens|"
                   r"stationery|goulet\s*pens)"),
    ("Electronics", r"(best\s*buy|apple\s*store|apple\.com(?!/bill)|b&h\s*photo|"
                    r"\bb&h\b|micro\s*center|newegg|adorama|electronics|samsung|"
                    r"framework\s*computer|monoprice)"),
    ("Clothing", r"(uniqlo|gap\b|old\s*navy|banana\s*republic|j\.?\s*crew|h&m|zara|"
                 r"nordstrom|macy'?s|patagonia|rei\b|lululemon|nike|adidas|everlane|"
                 r"madewell|levi'?s|ross\s*stores|tj\s*maxx|marshalls|clothing|apparel|"
                 r"shoes|footwear)"),
    ("Jewelry", r"(jewel|tiffany|kay\s*jewel|zales|pandora)"),
    ("Cosmetics", r"(sephora|ulta|cosmetic|glossier|lush\b|the\s*ordinary|beauty)"),
    ("Arts & Crafts", r"(michaels|joann|hobby\s*lobby|blick|art\s*supply|craft)"),
    ("Shipping", r"(usps|fedex|\bups\b|dhl|shipping|post\s*office|stamps\.com|"
                 r"pirate\s*ship)"),
    ("Gifts", r"(gift|1-800-flowers|flowers|florist|etsy|hallmark)"),
    # Giving and people.
    ("Charity", r"(donat|charity|red\s*cross|unicef|wikimedia|wikipedia|eff\.org|"
                r"aclu|givewell|gofundme|goodwill|salvation\s*army|nonprofit|"
                r"foundation|every\.org|oxfam|doctors\s*without|msf)"),
    ("Other People", r"(zelle|venmo|cash\s*app|\bsquare\s*cash|paypal\s*\*?\s*(to|p2p))"),
    # Financial.
    ("Immigration", r"(uscis|immigration|visa\s*(fee|application)|dhs\s*uscis|"
                    r"passport|ceac|vfs\s*global|bls\s*international|embassy|consulate)"),
    ("Taxes", r"(irs\b(?!.*ref)|franchise\s*tax|dept\s*of\s*revenue|tax\s*payment|"
              r"turbotax|h&r\s*block|freetaxusa|property\s*tax|estimated\s*tax)"),
    ("Financial Fees", r"(\bfee\b|fees\b|overdraft|nsf|late\s*charge|finance\s*charge|"
                       r"interest\s*charge|annual\s*membership|foreign\s*transaction)"),
    ("Financial & Legal Services", r"(attorney|law\s*(office|firm)|legal|notary|cpa\b|"
                                   r"accountant|legalzoom|rocket\s*lawyer|financial\s*"
                                   r"advisor)"),
    ("Cash & ATM", r"(\batm\b|cash\s*withdraw|withdrawal\s*-?\s*atm|cash\s*back)"),
    ("Checks", r"(\bcheck\s*#?\s*\d+|\bchk\s*#?\s*\d+|\bcheck\b\s*paid|mobile\s*deposit)"),
]

# Every keyword starts a word: "aws" is not in "laws", "gap" is not in "singapore".
_COMPILED = [
    (name, re.compile(rf"(?<![a-z0-9])(?:{pattern})", re.I)) for name, pattern in _KEYWORDS
]

# What a merchant key keeps: letters only, first three words. "SQ *BLUE BOTTLE
# COFFEE #12 OAKLAND CA" and "BLUE BOTTLE COFFEE 0042" should be one merchant.
_NOISE = re.compile(r"\b(sq|tst|pos|dbt|debit|purchase|card|visa|ach|www|com|inc|llc)\b")
_NON_ALPHA = re.compile(r"[^a-z]+")


def merchant_key(merchant: str | None, description: str | None) -> str:
    text = (merchant or description or "").casefold()
    text = _NON_ALPHA.sub(" ", text)
    text = _NOISE.sub(" ", text)
    words = [w for w in text.split() if len(w) > 1]
    return " ".join(words[:3])


@dataclass(frozen=True)
class Choice:
    category_id: uuid.UUID
    reason: str  # "history" | "keyword" | "name" | "transfer"


@dataclass
class Categorizer:
    """Built once per household per run; ``suggest`` is then pure and fast."""

    by_name: dict[str, uuid.UUID]
    types: dict[uuid.UUID, str]
    custom_names: list[tuple[re.Pattern[str], uuid.UUID]]
    history: dict[str, Counter]

    def _allowed(self, category_id: uuid.UUID, amount) -> bool:
        kind = self.types.get(category_id)
        if kind == "transfer":
            return True
        if amount is None or amount == 0:
            return True
        return kind == ("income" if amount > 0 else "expense")

    def suggest(self, txn: Transaction, *, credit_legs: set[uuid.UUID] | None = None
                ) -> Choice | None:
        # 3. A linked transfer already says what it is.
        if txn.transfer_group_id is not None:
            name = (
                "Credit Card Payment"
                if credit_legs and txn.transfer_group_id in credit_legs
                else "Transfer"
            )
            cid = self.by_name.get(name.casefold()) or self.by_name.get("transfer")
            if cid:
                return Choice(cid, "transfer")

        # 1. History: a clear majority of what a human chose for this merchant.
        key = merchant_key(txn.merchant, txn.description)
        if key and key in self.history:
            ranked = self.history[key].most_common(2)
            top, n = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0
            if top in self.types and n > second:
                return Choice(top, "history")

        text = f"{txn.merchant or ''} {txn.description or ''}"
        # 2a. Keywords for the starter categories that exist in this household.
        for name, pattern in _COMPILED:
            cid = self.by_name.get(name.casefold())
            if cid is None or not self._allowed(cid, txn.amount):
                continue
            if pattern.search(text):
                return Choice(cid, "keyword")
        # 2b. A custom category's own name, as a whole word.
        for pattern, cid in self.custom_names:
            if self._allowed(cid, txn.amount) and pattern.search(text):
                return Choice(cid, "name")
        return None


async def load(session: AsyncSession) -> Categorizer:
    rows = (
        await session.execute(
            select(Category.id, Category.name, CategoryGroup.type).join(
                CategoryGroup, Category.group_id == CategoryGroup.id
            )
        )
    ).all()
    starter = {name.casefold() for name, _ in _KEYWORDS}
    by_name = {name.casefold(): cid for cid, name, _t in rows}
    types = {cid: kind for cid, _n, kind in rows}
    custom = []
    for cid, name, _t in rows:
        if name.casefold() in starter:
            continue
        words = [w for w in re.split(r"[^\w]+", name) if len(w) >= 4]
        if words:
            custom.append(
                (re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b", re.I), cid)
            )

    # History: rows a *human* (or a human's rule) categorized. The categorizer's own
    # past output is not evidence — learning from it would only entrench a guess.
    history: dict[str, Counter] = defaultdict(Counter)
    hist_rows = (
        await session.execute(
            select(Transaction.merchant, Transaction.description, Transaction.category_id,
                   Transaction.field_sources)
            .where(Transaction.category_id.is_not(None))
        )
    ).all()
    for merchant, description, cid, sources in hist_rows:
        if (sources or {}).get("category") not in (USER, RULE):
            continue
        key = merchant_key(merchant, description)
        if key:
            history[key][cid] += 1
    return Categorizer(by_name=by_name, types=types, custom_names=custom, history=dict(history))


async def _credit_groups(session: AsyncSession, group_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Transfer groups with a leg on a card or loan: paying one off."""
    if not group_ids:
        return set()
    rows = (
        await session.execute(
            select(Transaction.transfer_group_id)
            .join(Account, Account.id == Transaction.account_id)
            .where(
                Transaction.transfer_group_id.in_(group_ids),
                Account.type.in_(("credit", "loan")),
            )
        )
    ).scalars().all()
    return set(rows)


def _write(txn: Transaction, category_id: uuid.UUID) -> bool:
    if txn.category_id == category_id:
        return False
    txn.category_id = category_id
    sources = dict(txn.field_sources or {})
    sources["category"] = AUTO
    txn.field_sources = sources
    return True


async def categorize_blank(session: AsyncSession, txn_ids: list[uuid.UUID]) -> int:
    """Fill the category of each of ``txn_ids`` that has none. Returns how many.

    The automatic path, run by sync after the rules: it never replaces a value,
    whoever set it, so it can only add information.
    """
    if not txn_ids:
        return 0
    rows = (
        await session.execute(
            select(Transaction).options(selectinload(Transaction.splits)).where(
                Transaction.id.in_(txn_ids),
                Transaction.category_id.is_(None),
                Transaction.is_split_parent.is_(False),
            )
        )
    ).scalars().all()
    rows = [t for t in rows if (t.field_sources or {}).get("category") != USER]
    if not rows:
        return 0
    cat = await load(session)
    credit = await _credit_groups(
        session, {t.transfer_group_id for t in rows if t.transfer_group_id}
    )
    changed = 0
    for txn in rows:
        choice = cat.suggest(txn, credit_legs=credit)
        if choice and _write(txn, choice.category_id):
            changed += 1
    await session.flush()
    return changed


@dataclass
class CategorizeAllResult:
    examined: int = 0
    categorized: int = 0
    changed: int = 0
    left_blank: int = 0
    transfers_linked: int = 0


async def categorize_all(session: AsyncSession, household_id: uuid.UUID) -> CategorizeAllResult:
    """The admin's once-over: link transfers, then re-categorize **every** row.

    Overwrites whatever is there — the admin was told so before asking — except
    on split parents, whose categories live on their splits. A row the
    categorizer has no guess for keeps what it had rather than being blanked:
    "no idea" is not a reason to throw away an answer.

    The transfer matcher runs first, over every unlinked row, so a pair it can now
    link is filed as a transfer rather than guessed at as two separate rows.
    """
    result = CategorizeAllResult()
    unlinked = (
        await session.execute(
            select(Transaction.id).where(Transaction.transfer_group_id.is_(None))
            .order_by(Transaction.transacted_at, Transaction.id)
        )
    ).scalars().all()
    result.transfers_linked = await txn_service.auto_match_transfers(
        session, household_id, list(unlinked)
    )

    cat = await load(session)
    rows = (
        await session.execute(
            select(Transaction).where(Transaction.is_split_parent.is_(False))
        )
    ).scalars().all()
    credit = await _credit_groups(
        session, {t.transfer_group_id for t in rows if t.transfer_group_id}
    )
    for txn in rows:
        result.examined += 1
        choice = cat.suggest(txn, credit_legs=credit)
        if choice is None:
            result.left_blank += txn.category_id is None
            continue
        result.categorized += 1
        if _write(txn, choice.category_id):
            result.changed += 1
    await session.flush()
    return result
