"""CSV import (WS-IMP, M1a): parse → map → dedupe → commit.

An import is a *manual* write path. It inserts rows with no ``external_id``,
which is exactly ADR-0019's manual-origin boundary: a later sync merges only into
rows it owns, so it can never overwrite an imported row — if sync later brings
what looks like the same transaction, it lands as a separate row surfaced for
review. That is why these rows are ``source='csv'`` and not ``simplefin``.

Three properties this module exists to guarantee:

* **Preview writes nothing.** :func:`preview_csv` is pure — the mapping UI is
  built from the file and the household, never from stored state.
* **Dedupe is exact, and only exact.** ``import_hash`` covers the row's identity
  *plus its occurrence ordinal inside the file*, so two genuine same-day $5
  coffees both import while re-importing the same file collides with itself. An
  exact collision is counted and skipped; a near-miss is imported and flagged for
  review. Nothing is silently dropped and nothing is silently merged.
* **A bad row is an error, not a guess.** An unparseable date or amount, or an
  owner name that is not this household's, is reported with its file line number
  and left out — the alternative is a ledger row nobody can trust.

Money is ``Decimal`` end to end (ADR-0005); nothing here goes near ``float``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_storage
from app.models import Category, Owner, Transaction
from app.schemas.imports import MAPPABLE_FIELDS
from app.schemas.transactions import TransactionCreate
from app.services.errors import LedgerError
from app.services.ledger import get_account
from app.services.transactions import create_transaction

# ---- Limits (ARCHITECTURE §5: user-supplied files are size- and column-limited)

# A year of statement exports is well under this; anything larger is not a bank
# CSV, and bounding it is what keeps the parse (and the response) finite.
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5_000
MAX_COLUMNS = 100
SAMPLE_ROWS = 10

# "Same account and amount, transacted_at within a day or two" — two days each
# way covers weekend/holiday posting lag without dragging in unrelated spending.
NEAR_DUPLICATE_DAYS = 2

_AMOUNT_FIELDS = ("amount", "debit", "credit")


class RowError(ValueError):
    """A problem confined to one row. The row is reported, never guessed at."""


@dataclass(frozen=True)
class RowIssue:
    line: int
    message: str


@dataclass
class CommitResult:
    inserted: int = 0
    skipped: int = 0
    suspects: int = 0
    errors: list[RowIssue] = field(default_factory=list)


@dataclass(frozen=True)
class Preview:
    headers: list[str]
    sample: list[list[str]]
    suggested: dict[str, str | None]


# ---- Parsing ---------------------------------------------------------------


@dataclass(frozen=True)
class Table:
    """A parsed CSV.

    ``headers`` are display names, made unique and non-empty so they can key the
    mapping: a file with two "Amount" columns or a blank header cell still has to
    be mappable column by column.
    """

    headers: list[str]
    index: dict[str, int]
    rows: list[list[str]]
    # Parallel to ``rows``: the file line each one came from. Kept because a row
    # error has to name the line a text editor shows, and blank lines are skipped
    # rather than parsed, so counting rows would drift after the first blank.
    lines: list[int]

    def cell(self, row: Sequence[str], header: str | None) -> str:
        if header is None:
            return ""
        i = self.index[header]
        return row[i].strip() if i < len(row) else ""


_DELIMITERS = (",", ";", "\t")


def _decode(raw: bytes) -> str:
    if len(raw) > MAX_FILE_BYTES:
        raise LedgerError(f"File is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB", 400)
    try:
        # utf-8-sig, so the BOM Excel writes on export is dropped rather than
        # glued to the first header name (which would make "Date" unmappable).
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # cp1252 is what a Windows bank tool actually writes. Refusing the file
        # would be less honest than reading it in the encoding it is really in.
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise LedgerError("File is not valid UTF-8 or Windows-1252 text", 400) from exc


def _delimiter(first_line: str) -> str:
    """Pick the separator by counting, not by sniffing.

    Bank exports are comma-separated; a European or locale-Excel export uses a
    semicolon or a tab. Counting is deterministic and gives the same answer at
    preview and at commit, which is what lets the previewed mapping still apply.
    """
    return max(_DELIMITERS, key=first_line.count)


def parse_table(raw: bytes) -> Table:
    text = _decode(raw)
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if not first:
        raise LedgerError("File is empty", 400)

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(first))
    header_row: list[str] | None = None
    rows: list[list[str]] = []
    line_numbers: list[int] = []
    for record in reader:
        # A line with nothing on it is not a row: counting a trailing newline as
        # one would make a clean export look like it had errors.
        if not any(c.strip() for c in record):
            continue
        if header_row is None:
            header_row = record
        else:
            rows.append(record)
            line_numbers.append(reader.line_num)
    if header_row is None:
        raise LedgerError("File is empty", 400)
    if len(header_row) > MAX_COLUMNS:
        raise LedgerError(f"File has more than {MAX_COLUMNS} columns", 400)

    names: list[str] = []
    index: dict[str, int] = {}
    seen: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        base = cell.strip() or f"column {i + 1}"
        n = seen.get(base, 0) + 1
        seen[base] = n
        name = base if n == 1 else f"{base} ({n})"
        names.append(name)
        index[name] = i

    if not rows:
        raise LedgerError("File has a header but no data rows", 400)
    if len(rows) > MAX_ROWS:
        raise LedgerError(f"File has more than {MAX_ROWS} data rows", 400)
    return Table(headers=names, index=index, rows=rows, lines=line_numbers)


# ---- Header names → fields -------------------------------------------------

_NORMALIZE = re.compile(r"[^a-z0-9]+")

# Suggested by header name alone: this is a starting point a human corrects, so
# it errs towards the spellings real exports use rather than being exhaustive.
_SYNONYMS: dict[str, str] = {
    "date": "date",
    "posted at": "date",
    "posted date": "date",
    "date posted": "date",
    "posting date": "date",
    "transaction date": "date",
    "trans date": "date",
    "amount": "amount",
    "transaction amount": "amount",
    "debit": "debit",
    "debit amount": "debit",
    "withdrawal": "debit",
    "credit": "credit",
    "credit amount": "credit",
    "deposit": "credit",
    "description": "description",
    "memo": "description",
    "payee": "description",
    "category": "category",
    "owner": "owner",
    "notes": "notes",
    "note": "notes",
}


def _normalize(name: str) -> str:
    return _NORMALIZE.sub(" ", name.strip().lower()).strip()


def suggest_mapping(headers: Sequence[str]) -> dict[str, str | None]:
    """Suggest a field per column, first column wins.

    A field is suggested at most once: two columns both claiming "amount" would
    make the mapping ambiguous, and the importer must not pick one for the user.
    """
    out: dict[str, str | None] = {}
    taken: set[str] = set()
    for h in headers:
        f = _SYNONYMS.get(_normalize(h))
        if f and f not in taken:
            taken.add(f)
            out[h] = f
        else:
            out[h] = None
    return out


def validate_mapping(headers: Sequence[str], mapping: dict[str, str | None]) -> None:
    """Reject a mapping the importer cannot act on, naming what is missing.

    Called with the *suggested* mapping at preview and the *confirmed* one at
    commit, so a file that cannot possibly import is a clean 400 up front instead
    of a mapping UI that leads nowhere.
    """
    unknown = sorted(set(mapping) - set(headers))
    if unknown:
        raise LedgerError(
            f"Mapping names columns this file does not have: {', '.join(unknown)}", 400
        )
    used = sorted(f for f in mapping.values() if f)
    bad = [f for f in used if f not in MAPPABLE_FIELDS]
    if bad:
        raise LedgerError(
            f"Unknown field(s) in the mapping: {', '.join(bad)}. "
            f"Expected one of {', '.join(MAPPABLE_FIELDS)}.", 400
        )
    # One field, one column: two columns both claiming "date" is an ambiguity the
    # importer must not resolve by silently picking one.
    if len(used) != len(set(used)):
        raise LedgerError("Two columns are mapped to the same field", 400)
    if "date" not in used:
        raise LedgerError(
            "No column is mapped to a date — every imported row needs one. "
            "Map a column to 'date'.", 400
        )
    if not set(used) & set(_AMOUNT_FIELDS):
        raise LedgerError(
            f"No column is mapped to an amount — map one of "
            f"{', '.join(_AMOUNT_FIELDS)}.", 400
        )


# ---- Cell parsing ----------------------------------------------------------

_SYMBOLS = "$€£¥₹₩"
# A bare currency decoration: "USD", "$", "USD$" — a whole token of nothing else.
_CURRENCY_TOKEN = re.compile(rf"^[{_SYMBOLS}]*(?:[A-Za-z]{{3}})?[{_SYMBOLS}]*$")


def _separators_to_dot(s: str) -> str:
    """Resolve '.' and ',' into a plain decimal string.

    Both present: the *last* one is the decimal point, so "1,234.56" and
    "1.234,56" both become 1234.56. Only a comma: a single trailing group of
    exactly two digits is a decimal comma ("12,50"), anything else is a
    thousands separator ("1,234", "1,234,567"). That two-digit case is a real
    ambiguity — "1,50" is 1.50 here and 150 in a strict-US file — and it is
    resolved the way the exports this was built against write it, documented
    rather than quietly decided per row.
    """
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            return s.replace(".", "").replace(",", ".")
        return s.replace(",", "")
    if "," in s:
        if s.count(",") == 1 and len(s.split(",")[1]) == 2:
            return s.replace(",", ".")
        return s.replace(",", "")
    return s


def parse_amount(raw: str) -> Decimal:
    """Parse one money cell, tolerating what bank exports actually contain.

    Handles a leading currency symbol or a trailing ISO code, thousands
    separators, parentheses for negatives, and a trailing minus. Anything else
    alphabetic is refused rather than stripped: "1e5" must not quietly become 15,
    and a row that cannot be read is a reported error, not a fabricated amount.
    """
    s = raw.strip()
    if not s:
        raise RowError("no amount in this row")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.endswith("-"):
        negative = True
        s = s[:-1].strip()
    s = "".join(t for t in s.split() if t and not _CURRENCY_TOKEN.fullmatch(t))
    s = s.strip(_SYMBOLS + " ")
    if not s or any(ch.isalpha() for ch in s):
        raise RowError(f"unrecognised amount {raw.strip()!r}")
    try:
        value = Decimal(_separators_to_dot(s))
    except (InvalidOperation, ValueError) as exc:
        raise RowError(f"unrecognised amount {raw.strip()!r}") from exc
    magnitude = abs(value)
    # A parenthesised or trailing-minus cell is negative; an explicit leading
    # minus in the digits agrees with it, so either signal alone is enough.
    return -magnitude if (negative or value < 0) else magnitude


def _iso_date(s: str) -> date | None:
    """ISO 8601, and the slashed year-first spelling some exports use instead.

    A year-first date has no order to be ambiguous about, which is why it is
    tried before the two orders below rather than being left to them: "2026/01/08"
    read as an order would be a row error, and it is not one.
    """
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%Y/%m/%d").date()
    except ValueError:
        return None


# Dash and slash forms of both orders. The two-year variants are here because
# plenty of exports still write them.
_US_ORDERS = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y")
_EU_ORDERS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y")


def parse_date(raw: str, *, dayfirst: bool = False) -> date:
    """Parse one date cell. ISOs and unambiguous values first, order last.

    A CSV date carries no timezone and no order, so "05/03/2026" is genuinely
    ambiguous: the commit's ``dayfirst`` option decides it (US-first by default)
    and this is the only place that decides. A value that parses one way and not
    the other ("13/05/2026") is read the only way it can be — that is not a
    guess, and refusing it would just be pedantry.
    """
    s = raw.strip()
    if not s:
        raise RowError("no date in this row")
    iso = _iso_date(s)
    if iso is not None:
        return iso
    first, second = (_EU_ORDERS, _US_ORDERS) if dayfirst else (_US_ORDERS, _EU_ORDERS)
    for fmt in (*first, *second):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise RowError(
        f"unrecognised date {raw.strip()!r} — expected ISO (2026-01-05 or 2026/01/05) or "
        f"mm/dd/yyyy (commit with dayfirst for dd/mm/yyyy)"
    )


def _transacted_at(d: date) -> datetime:
    """A date-only export means "this calendar day" everywhere, so it is stored
    as noon UTC: midnight would fall on the previous day for a household west of
    UTC and shift the row's month at the boundary (the same reason the manual
    entry form uses noon)."""
    return datetime(d.year, d.month, d.day, 12, tzinfo=UTC)


# ---- Preview ---------------------------------------------------------------


def preview_csv(raw: bytes, *, sample_rows: int = SAMPLE_ROWS) -> Preview:
    """Headers, a sample, and a suggested mapping. Writes nothing."""
    table = parse_table(raw)
    suggested = suggest_mapping(table.headers)
    validate_mapping(table.headers, suggested)
    width = len(table.headers)
    sample = [list(r[:width]) + [""] * max(0, width - len(r)) for r in table.rows[:sample_rows]]
    return Preview(headers=table.headers, sample=sample, suggested=suggested)


# ---- Commit ----------------------------------------------------------------


def import_hash(
    account_id: uuid.UUID,
    transacted_at: datetime,
    amount: Decimal,
    description: str | None,
    ordinal: int,
) -> str:
    """sha256 over the row's identity plus where it sat in the file.

    The ordinal is the 0-based count of *identical preceding rows in the same
    file*, which is what separates two genuine identical rows — two $5 coffees —
    from a re-import of the same file: the genuine pair differ by ordinal, while
    the re-import reproduces both digests exactly (ARCHITECTURE §2).

    The amount is quantized first so "5" and "5.00" hash the same, and so the
    digest describes the value NUMERIC(19,4) actually stores.
    """
    payload = "|".join((
        str(account_id),
        transacted_at.isoformat(),
        format(quantize_storage(amount), "f"),
        description or "",
        str(ordinal),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _name_map(session: AsyncSession, model: type) -> dict[str, uuid.UUID]:
    """``lower(name) → id`` for a household-scoped naming table.

    The query runs in the request's household-scoped session, so a name that
    exists only in another household simply is not in the map: nothing here can
    resolve a foreign owner or category, by construction rather than by a check
    somebody has to remember to write.
    """
    rows = (await session.execute(select(model.id, model.name))).all()
    out: dict[str, uuid.UUID] = {}
    for row_id, name in rows:
        out.setdefault(name.strip().lower(), row_id)
    return out


def _by_field(mapping: dict[str, str | None]) -> dict[str, str]:
    return {f: h for h, f in mapping.items() if f}


def _row_amount(table: Table, row: Sequence[str], by_field: dict[str, str]) -> Decimal:
    """Resolve a row's amount from whichever source it has.

    A ``debit``/``credit`` pair carries unsigned magnitudes, and money out is
    negative in the ledger, so debit is negated and credit is not. A row with
    both takes debit — a file that fills both columns is describing one movement,
    and the out-side is the one that must not be lost. An ``amount`` cell wins
    when it is there, which is what makes a file with a signed amount column *and*
    leftover debit/credit columns behave.
    """
    signed = table.cell(row, by_field.get("amount"))
    if signed:
        return parse_amount(signed)
    debit = table.cell(row, by_field.get("debit"))
    if debit:
        return -abs(parse_amount(debit))
    credit = table.cell(row, by_field.get("credit"))
    if credit:
        return abs(parse_amount(credit))
    raise RowError("no amount in this row")


async def _near_duplicate(
    session: AsyncSession, account_id: uuid.UUID, amount: Decimal, when: datetime
) -> bool:
    """Is there already a same-amount row on this account within a couple of days?

    Anything this finds is a *possible* duplicate, never a match: the ledger
    cannot tell two real coffees from a re-import that changed its description,
    so the row imports and a human decides (ARCHITECTURE §2, ADR-0019).
    """
    window = timedelta(days=NEAR_DUPLICATE_DAYS)
    found = (
        await session.execute(
            select(Transaction.id)
            .where(
                Transaction.account_id == account_id,
                Transaction.amount == amount,
                Transaction.transacted_at >= when - window,
                Transaction.transacted_at <= when + window,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def commit_csv(
    session: AsyncSession,
    household_id: uuid.UUID,
    *,
    raw: bytes,
    mapping: dict[str, str | None],
    account_id: uuid.UUID,
    default_category_id: uuid.UUID | None = None,
    dayfirst: bool = False,
) -> CommitResult:
    """Import every usable row of ``raw`` into ``account_id``.

    Runs entirely in the caller's transaction: the request opens one, so a file
    either lands whole or not at all. The per-row savepoint below is not a
    per-row commit — it only keeps an unexpected unique-index collision from
    taking the whole file down with it.
    """
    table = parse_table(raw)
    validate_mapping(table.headers, mapping)
    by_field = _by_field(mapping)

    # Resolve the target before touching a row, so importing into an unknown or
    # another household's account is a 404 and not a partial file.
    await get_account(session, account_id)
    if default_category_id is not None:
        # Resolved under RLS for the same reason: a foreign id must be a 404, not
        # a foreign-key 500 part-way through the file.
        found = (
            await session.execute(select(Category.id).where(Category.id == default_category_id))
        ).scalar_one_or_none()
        if found is None:
            raise LedgerError("Category not found", 404)
    owners = await _name_map(session, Owner)
    categories = await _name_map(session, Category)

    # Pass 1 — parse. The ordinal counts every row whose identity we could read,
    # including rows a later step rejects, so the digests depend on the file
    # alone: fixing one bad cell and re-running produces the same hashes for
    # every untouched row instead of orphaning them as new inserts.
    parsed: list[tuple[int, TransactionCreate, str]] = []
    result = CommitResult()
    ordinals: dict[tuple, int] = {}
    for offset, row in enumerate(table.rows):
        line = table.lines[offset]
        try:
            amount = _row_amount(table, row, by_field)
            when = _transacted_at(parse_date(
                table.cell(row, by_field.get("date")), dayfirst=dayfirst
            ))
        except RowError as exc:
            result.errors.append(RowIssue(line, str(exc)))
            continue
        description = table.cell(row, by_field.get("description")) or None
        key = (when, quantize_storage(amount), description)
        ordinal = ordinals.get(key, 0)
        ordinals[key] = ordinal + 1
        owner_id: uuid.UUID | None = None
        owner_cell = table.cell(row, by_field.get("owner"))
        if owner_cell:
            owner_id = owners.get(owner_cell.strip().lower())
            if owner_id is None:
                result.errors.append(RowIssue(
                    line,
                    f"unknown owner {owner_cell.strip()!r} — add that owner, or leave the "
                    f"cell blank to inherit the account's",
                ))
                continue
        category_id = default_category_id
        category_cell = table.cell(row, by_field.get("category"))
        if category_cell:
            # A bank's category vocabulary is not the household's, so an unknown
            # name falls back to the commit-time default (or uncategorized)
            # instead of failing the row: the balance is still right, and the row
            # lands as needs_review for a human to file properly.
            category_id = categories.get(category_cell.strip().lower(), default_category_id)
        parsed.append((
            line,
            TransactionCreate(
                account_id=account_id,
                amount=amount,
                transacted_at=when,
                description=description,
                category_id=category_id,
                owner_id=owner_id,
                notes=table.cell(row, by_field.get("notes")) or None,
            ),
            import_hash(account_id, when, amount, description, ordinal),
        ))

    # Pass 2 — dedupe. One query for the whole file rather than one per row; the
    # digests within a file are unique by construction (ordinals), so nothing
    # this batch reports is invalidated by an insert later in the loop.
    digests = {digest for _line, _data, digest in parsed}
    already = set()
    if digests:
        already = set(
            (
                await session.execute(
                    select(Transaction.import_hash).where(
                        Transaction.account_id == account_id,
                        Transaction.import_hash.in_(digests),
                    )
                )
            ).scalars().all()
        )

    for _line, data, digest in parsed:
        if digest in already:
            result.skipped += 1
            continue
        suspect = await _near_duplicate(
            session, account_id, quantize_storage(data.amount), data.transacted_at
        )
        try:
            # The unique index on (account_id, import_hash) is the backstop the
            # query above cannot be: two commits of the same file racing each
            # other both pass the check, and the loser must count a skip rather
            # than fail the whole file. A savepoint scopes that to the one row.
            async with session.begin_nested():
                txn = await create_transaction(session, household_id, data, source="csv")
                txn.import_hash = digest
                if suspect:
                    # A possible duplicate goes to review, not to the bin and not
                    # silently merged (ARCHITECTURE §2).
                    txn.review_status = "needs_review"
                await session.flush()
        except IntegrityError:
            result.skipped += 1
            continue
        result.inserted += 1
        if suspect:
            result.suspects += 1

    return result
