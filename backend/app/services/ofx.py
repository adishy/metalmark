"""OFX/QFX parsing (ADR-0030): 2.x only, and it says so when it is not.

OFX is two incompatible file formats wearing one name. **1.x is SGML** — an
unquoted, colon-delimited header block followed by tags whose values are not
always quoted and not always closed. **2.x is XML.** They share a tag vocabulary
and nothing else, and the SGML dialect has no specification strict enough that
two implementations agree on where a value ends.

So this module reads 2.x and *refuses* 1.x by name, with a message that says what
the file is and what to do instead (ADR-0030 §1). That refusal is the entire
mitigation: to read 1.x with an XML parser you must first guess where unquoted
values end, and a guess that mis-splits one ``<NAME>`` silently corrupts a
merchant while a guess that mis-splits one ``<TRNAMT>`` silently corrupts an
amount. That is the one class of bug in this workstream that lands wrong money in
the ledger with no error anywhere, and the CSV path already covers the case.

Three properties this module exists to guarantee:

* **The parse is bounded and defused.** ``MAX_FILE_BYTES`` is checked before the
  parser is handed anything, and the parser is ``defusedxml`` — a user-supplied
  XML file is exactly the input where ``xml.etree``'s entity expansion turns a
  few hundred bytes into gigabytes. ADR-0030 §2 pre-decided this; this is the
  dependency's first call site.
* **Nothing downstream sees XML.** :func:`parse_ofx` returns dataclasses with
  ``Decimal`` amounts and real datetimes, so the importer never walks a tree and
  never re-reads a string as money.
* **A row that cannot be read is a reported error, not a guess.** Same policy as
  the CSV importer: a ``<TRNAMT>`` that is not a decimal is dropped with its
  position named, never coerced.

Money is ``Decimal`` end to end (ADR-0005): every amount here goes through
:func:`_money`, which refuses anything that is not plain decimal notation —
``Decimal("NaN")`` and ``Decimal("Infinity")`` both parse, and both would raise
somewhere far away from the cause.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.services.errors import LedgerError

#: The same bound the CSV path enforces (``imports.MAX_FILE_BYTES``), checked
#: here because here is where the bytes reach a parser. Defined rather than
#: imported because the dependency runs one way: ``imports`` imports this module.
MAX_FILE_BYTES = 2 * 1024 * 1024

#: How much of the file the format sniff reads. Generous for a header block that
#: is six short lines, tiny against the file, and — the point — it means a
#: mislabelled 4 MB upload is still a cheap "this is not an OFX file".
_HEAD_BYTES = 1024

_BOM = b"\xef\xbb\xbf"

OFX_1X = "1.x"
OFX_2X = "2.x"
NOT_OFX = "not-ofx"

#: ADR-0030 §1's message, and the reason this module exists in the shape it does.
#: It names the format found and the way out, because the alternative — guessing
#: at SGML boundaries — is a wrong number in the ledger with nothing to show for
#: it. Nothing here is user-supplied text: the file's bytes never reach it.
OFX_1X_REFUSAL = (
    "This is an OFX 1.x file, which MetalMark does not read. OFX 1.x is SGML, "
    "not XML — its tags are unquoted and are often not closed — so reading it "
    "would mean guessing where each value ends, and a wrong guess puts a wrong "
    "amount in the ledger silently. Almost every bank that still exports 1.x "
    "also offers OFX 2.x, QFX or CSV: download one of those, or export the "
    "statement as CSV and import it here."
)


# ---- Detection -------------------------------------------------------------


def _sniff(raw: bytes) -> bytes:
    """The first non-whitespace, BOM-free bytes of the file.

    Both strips matter: a BOM is what Windows tools write and it would otherwise
    hide ``OFXHEADER:`` behind three bytes nobody can see, and leading blank
    lines are common in files that have been through a text editor.
    """
    head = raw[:_HEAD_BYTES].lstrip()
    if head.startswith(_BOM):
        head = head[len(_BOM):].lstrip()
    return head


def detect_format(raw: bytes) -> str:
    """``OFX_1X``, ``OFX_2X`` or ``NOT_OFX``, from the leading bytes alone.

    Detection is by the header each dialect starts with, never by trying to parse
    and seeing what happens: a 1.x file is well-formed enough to fool a lenient
    reader for a while, and by the time it does not, a value has already been
    mis-split.
    """
    head = _sniff(raw)
    if head.startswith(b"OFXHEADER:"):
        return OFX_1X
    if head.startswith(b"<?xml") or head.startswith(b"<?OFX"):
        return OFX_2X
    return NOT_OFX


# ---- The parsed shape ------------------------------------------------------


@dataclass(frozen=True)
class OfxTransaction:
    """One ``<STMTTRN>``, normalized.

    ``fitid`` is the institution's own identifier and is ``None`` when the file
    omits it — the importer's dedupe key falls back to ``import_hash`` then
    (ADR-0030 §3), which is why this is optional rather than refused here.
    """

    fitid: str | None
    amount: Decimal
    transacted_at: datetime
    posted_at: datetime
    description: str | None
    check_number: str | None
    trn_type: str | None


@dataclass(frozen=True)
class OfxRowError:
    """A ``<STMTTRN>`` this parser refused to read, and where it sat.

    ``position`` is 1-based within the statement's own ``<BANKTRANLIST>``: OFX has
    no line numbers to point at, and "the third transaction in the file" is what
    a human can actually go and look at.
    """

    position: int
    message: str


@dataclass(frozen=True)
class OfxStatement:
    """One statement block (``<STMTRS>``, ``<CCSTMTRS>`` or ``<INVSTMTRS>``)."""

    acct_id: str | None
    acct_type: str | None
    currency: str | None
    start: datetime | None
    end: datetime | None
    ledger_balance: Decimal | None
    ledger_balance_at: datetime | None
    transactions: tuple[OfxTransaction, ...]
    #: Investment rows found in this block's ``<INVTRANLIST>``, counted and not
    #: parsed (ADR-0030 §5).
    investment_count: int
    #: Rows that could not be read honestly, reported rather than guessed at.
    errors: tuple[OfxRowError, ...] = ()


@dataclass(frozen=True)
class OfxFile:
    """A parsed OFX 2.x file: statement info plus banking rows.

    A file may hold more than one statement block (a bank that exports two
    accounts at once). All of their banking rows are returned, because the human
    chooses the account to import into and the file's own ``<ACCTID>`` is
    reported rather than obeyed (ADR-0030 §4) — so merging them costs nothing
    that the preview does not already say out loud. The *first* block is the one
    the preview describes.
    """

    org: str | None
    statements: tuple[OfxStatement, ...]

    @property
    def primary(self) -> OfxStatement:
        return self.statements[0]

    @property
    def transactions(self) -> tuple[OfxTransaction, ...]:
        return tuple(t for s in self.statements for t in s.transactions)

    @property
    def investment_count(self) -> int:
        return sum(s.investment_count for s in self.statements)

    @property
    def errors(self) -> tuple[OfxRowError, ...]:
        return tuple(e for s in self.statements for e in s.errors)


# ---- Parsing helpers -------------------------------------------------------


def _tag(elem) -> str:
    """The element's name, uppercased, with any namespace stripped.

    OFX 2.x uses the same tag names as 1.x and normally carries no namespace, but
    a file that went through a namespace-aware tool can arrive with one, and a
    reader that matched on the raw tag would then find nothing at all.
    """
    tag = elem.tag
    if not isinstance(tag, str):  # comments and processing instructions
        return ""
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    return tag.upper()


def _walk(elem) -> Iterator:
    """Every descendant, in document order, iteratively.

    Iterative on purpose: ``<a><a><a>…`` nests as deep as the file is long, and a
    recursive walk over a hostile 2 MB file is a ``RecursionError`` that arrives
    as a 500 rather than as a refusal.
    """
    stack = list(elem)
    stack.reverse()
    while stack:
        node = stack.pop()
        yield node
        children = list(node)
        children.reverse()
        stack.extend(children)


def _first(elem, name: str):
    # ``None`` in, ``None`` out: every lookup here is of the form "the <BALAMT>
    # under the <LEDGERBAL>", and a statement with no <LEDGERBAL> is an ordinary
    # statement rather than an error.
    if elem is None:
        return None
    for node in _walk(elem):
        if _tag(node) == name:
            return node
    return None


def _text(elem) -> str | None:
    if elem is None or elem.text is None:
        return None
    value = elem.text.strip()
    return value or None


def _text_of(elem, name: str) -> str | None:
    return _text(_first(elem, name))


def _children(elem, name: str) -> list:
    return [c for c in list(elem) if _tag(c) == name]


_DATE = re.compile(r"^(\d{8})(\d{6})?")
_ZONE = re.compile(r"\[([+-]?\d+(?:\.\d+)?):")
_AMOUNT = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _zone(value: str) -> tzinfo:
    """The ``[-5:EST]`` suffix OFX puts on a timestamp, as a real offset.

    The offset is applied rather than ignored because these timestamps are stored
    as instants and bucketed into months: a 23:30 Eastern charge read as UTC
    lands on the next day, and at a month boundary that is a report that disagrees
    with the bank's own statement. A file that omits the suffix is read as UTC —
    there is nothing better to do with it, and the alternative is refusing a file
    the CSV path would have taken.
    """
    found = _ZONE.search(value)
    if found is None:
        return UTC
    minutes = int(Decimal(found.group(1)) * 60)
    return timezone(timedelta(minutes=minutes))


def _timestamp(raw: str | None, *, noon: bool = False) -> datetime | None:
    """An OFX date or datetime as an aware UTC datetime.

    A date with no clock (``20260105``) means the calendar day, so it becomes
    noon UTC — the same choice the CSV path makes and for the same reason
    (``imports._transacted_at``): midnight would fall on the previous day for a
    household west of UTC and move the row's month at the boundary.
    """
    if raw is None:
        return None
    text = raw.strip()
    found = _DATE.match(text)
    if found is None:
        return None
    day, clock = found.group(1), found.group(2)
    if clock is None and not noon:
        clock = "120000"
    try:
        naive = datetime.strptime(day + (clock or "120000"), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=_zone(text)).astimezone(UTC)


def _money(raw: str | None) -> Decimal | None:
    """A ``Decimal`` or ``None``. Never ``float``, never a guess (ADR-0005).

    The regex is not decoration: ``Decimal`` accepts ``NaN`` and ``Infinity`` as
    values, and neither survives being stored in ``NUMERIC(19,4)`` or compared in
    the ledger. An OFX amount is always plain decimal notation, so anything else
    is a row this parser refuses to read.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not _AMOUNT.match(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:  # unreachable given the regex; belt and braces
        return None


def _parse_xml(raw: bytes):
    """``defusedxml``'s parser, with the two failures it exists for named."""
    if len(raw) > MAX_FILE_BYTES:
        raise LedgerError(
            f"File is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB", 400
        )
    try:
        return fromstring(raw)
    except DefusedXmlException as exc:
        # The exception's own text is deliberately not echoed: for an entity bomb
        # it would include the entity's *value*, which is the thing an attacker
        # chose in order to fill a log or a response.
        raise LedgerError(
            f"This file's XML is refused as unsafe ({type(exc).__name__}): it "
            f"declares a document type or an entity. Expanding entities from an "
            f"untrusted file is how a few hundred bytes become a gigabyte of "
            f"memory. Re-download the statement, or import it as CSV.",
            400,
        ) from exc
    except ParseError as exc:
        raise LedgerError(f"The file is not well-formed XML: {exc}", 400) from exc


# ---- The parse -------------------------------------------------------------

#: Statement blocks. A bank statement, a credit-card statement and a brokerage
#: statement are three different containers with one shape of contents.
_STATEMENT_TAGS = ("STMTRS", "CCSTMTRS", "INVSTMTRS")


def _transaction(elem) -> OfxTransaction | OfxRowError:
    """One ``<STMTTRN>``, or the reason it cannot be read.

    An unreadable row is returned as its error rather than dropped, so the count
    it lands in is a *number of rows the file had* and not a number of rows that
    happened to survive — the difference is the file lying about its own contents.
    """
    raw_amount = _text_of(elem, "TRNAMT")
    amount = _money(raw_amount)
    posted = _timestamp(_text_of(elem, "DTPOSTED"))
    if amount is None:
        return OfxRowError(
            0,
            f"unreadable amount {raw_amount!r} — an OFX amount is a plain decimal"
            if raw_amount
            else "no <TRNAMT> in this transaction",
        )
    if posted is None:
        return OfxRowError(0, f"unreadable date {_text_of(elem, 'DTPOSTED')!r}")
    initiated = _timestamp(_text_of(elem, "DTUSER"))
    name = _text_of(elem, "NAME") or _text_of(elem, "EXTDNAME")
    memo = _text_of(elem, "MEMO")
    # The description is the memo when there is one and the payee otherwise, and
    # ``merchant`` is deliberately left for the rules rather than filled from
    # ``<NAME>``: ``create_transaction`` marks a supplied merchant ``user`` and a
    # human's field outranks every rule, which would silently stop a rule that
    # renames merchants from ever touching an imported row (ADR-0030 §7).
    description = memo or name
    return OfxTransaction(
        fitid=_text_of(elem, "FITID"),
        amount=amount,
        transacted_at=initiated or posted,
        posted_at=posted,
        description=description,
        check_number=_text_of(elem, "CHECKNUM"),
        trn_type=_text_of(elem, "TRNTYPE"),
    )


def _statement(elem) -> OfxStatement:
    tranlist = _first(elem, "BANKTRANLIST")
    raw_transactions = _children(tranlist, "STMTTRN") if tranlist is not None else []
    transactions: list[OfxTransaction] = []
    errors: list[OfxRowError] = []
    for position, node in enumerate(raw_transactions, start=1):
        parsed = _transaction(node)
        if isinstance(parsed, OfxRowError):
            errors.append(OfxRowError(position, parsed.message))
        else:
            transactions.append(parsed)

    ledger = _first(elem, "LEDGERBAL")
    return OfxStatement(
        acct_id=_text_of(elem, "ACCTID"),
        acct_type=_text_of(elem, "ACCTTYPE"),
        currency=_text_of(elem, "CURDEF"),
        start=_timestamp(_text_of(tranlist, "DTSTART")),
        end=_timestamp(_text_of(tranlist, "DTEND")),
        ledger_balance=_money(_text_of(ledger, "BALAMT")),
        ledger_balance_at=_timestamp(_text_of(ledger, "DTASOF")),
        transactions=tuple(transactions),
        investment_count=0,
        errors=tuple(errors),
    )


#: The two children of an ``<INVTRANLIST>`` that are not transactions. Everything
#: else in it is one, which is what makes counting by exclusion here safer than a
#: list of the tags we happen to know: a bank that emits ``<BUYDEBT>`` or a tag
#: from a later spec revision is counted rather than silently skipped.
_INVTRANLIST_META = frozenset({"DTSTART", "DTEND"})


def _count_investments(root) -> int:
    """Every transaction row under an ``<INVTRANLIST>``, counted.

    Counted and not parsed: ``<BUYSTOCK>`` is not a cash outflow (ADR-0030 §5),
    and importing it as one double-counts the movement the moment investments
    exist as first-class objects. A count is what makes an all-investment file
    import as "0 imported, 40 skipped" rather than as a silent success.
    """
    total = 0
    for node in _walk(root):
        if _tag(node) == "INVTRANLIST":
            total += sum(1 for child in node if _tag(child) not in _INVTRANLIST_META)
    return total


def parse_ofx(raw: bytes) -> OfxFile:
    """Parse an OFX/QFX 2.x file into statements and banking rows.

    Raises :class:`LedgerError` — a 400 with a readable message — for anything
    that is not a 2.x file this importer can trust: a 1.x SGML file (by name),
    something that is not OFX at all, malformed XML, unsafe XML, a file too large
    to be a statement, or a well-formed OFX document with no statement in it.
    """
    kind = detect_format(raw)
    if kind == OFX_1X:
        raise LedgerError(OFX_1X_REFUSAL, 400)
    if kind == NOT_OFX:
        raise LedgerError(
            "This is not an OFX or QFX file: it starts with neither an OFX header "
            "block nor an XML declaration. Import it as CSV instead.",
            400,
        )

    root = _parse_xml(raw)
    statements = tuple(
        _statement(node)
        for node in _walk(root)
        if _tag(node) in _STATEMENT_TAGS
    )
    if not statements:
        raise LedgerError(
            "This file is XML but holds no OFX bank statement — there is no "
            "<STMTRS>, <CCSTMTRS> or <INVSTMTRS> block in it.",
            400,
        )

    investments = _count_investments(root)
    if investments:
        # Attributed to the first statement, because it is a per-file count and
        # the preview describes one statement; the file total is what the commit
        # reports (``OfxFile.investment_count``).
        statements = (replace(statements[0], investment_count=investments), *statements[1:])

    return OfxFile(org=_text_of(root, "ORG"), statements=statements)
