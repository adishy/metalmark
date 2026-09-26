"""Portable export/import — the household's data as one document (ADR-0036).

Two artifacts, each independently useful and each independently testable:

* **The document** (``export_document`` / ``import_document``) is the canonical,
  lossless, re-importable form: a versioned JSON object, ``metalmark.export`` v1.
  It is what "move my household to another instance" means.
* **The CSV** (``transactions_csv``) is one account's transactions as a
  spreadsheet. It is deliberately shaped so that the file the export writes is a
  file ``services/imports`` already knows how to read: every column header is one
  of that module's ``_SYNONYMS``, so the mapping auto-suggests, and its date and
  amount parsing accepts what is written here verbatim. Re-importing an export
  into the account it came from is then a no-op through the *existing* dedupe
  rule rather than a new one — which is the point of writing the two modules'
  contracts to meet instead of inventing a third format.

**Money is a string, always** (ADR-0005). A JSON number is an IEEE double, and a
ledger that round-trips through one is a ledger that quietly changes a cent. The
export writes ``"1200.0000"``; the reader *refuses* a number rather than
converting it, because accepting one would mean the format has two spellings and
only one of them is exact.

**Credentials never leave.** ``account_connections.access_url_encrypted`` is a
live bank bearer token, and the column is absent from the document by
construction — there is no "redact" step a later edit could forget to call. The
connection exports as metadata only; a connection with no credential cannot sync,
so the import lands it as ``auth_error`` with ``last_error`` saying why, rather
than as a healthy connection that fails on the next cron.

**No id crosses the boundary, and every entity therefore has a natural key.** An
``id`` in the document is a *within-document reference* — how one entry names
another, as a split names its parent — and never the id the row gets here. The
import allocates fresh ids and the children follow through the remap.

That is not caution, it is arithmetic: primary keys are global while RLS hides
the rows they name, so a document's uuid for a rule is *invisible* to the target
household's lookup and still *taken* at insert time. Adopting a source id would
make the round trip fail whenever the source still exists — which is always.
Every entity is matched on a key instead: owner and tag name, category name
within its group, account ``external_key``, security ticker, ``(account,
external_id)`` for transactions, ``(name, priority)`` for a rule, and — for the
two entities a human cannot name — a connection by ``(provider, org_name)`` and a
transfer group by the legs it links.

**``base_amount`` is not in the document.** It is a cache of ``amount`` at a dated
rate (ADR-0017), so exporting it would be exporting a derived value the target can
compute for itself. The *rates* travel instead, and the cache is recomputed on
import — which is what makes an exported number reproduce exactly rather than
depending on whatever rates the target happens to hold.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_storage
from app.models import (
    Account,
    AccountConnection,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    FxRate,
    Holding,
    Household,
    InvestmentTransaction,
    Owner,
    OwnerIncomeProfile,
    Paystub,
    PaystubLine,
    RecurringSeries,
    Rule,
    Security,
    SecurityPrice,
    Tag,
    Transaction,
    TransactionSplit,
    TransactionTag,
    TransferGroup,
)
from app.models.investments import HOLDING_SOURCES
from app.services import fx
from app.services.balance_sign import LIABILITY_TYPES, positives_are_amounts_owed
from app.services.errors import LedgerError

FORMAT = "metalmark.export"
#: 2 since ADR-0043: a liability's balances are signed (debt negative). Version 1
#: documents are still read — ``_upgrade_v1`` brings their liability balances into
#: the signed convention first, so a backup taken before the change restores to
#: the same net worth it was taken at.
VERSION = 2
READABLE_VERSIONS = (1, VERSION)

#: A ceiling on an imported document, checked before it is parsed. The document is
#: the whole household, so the bound is generous — it is here to stop a wrong file
#: (a video, a database dump) from being read into memory, not to ration a real
#: export.
MAX_IMPORT_BYTES = 64 * 1024 * 1024


def dumps(document: dict[str, Any]) -> bytes:
    """The document as bytes: UTF-8, indented, and stable.

    ``sort_keys`` is off and the key order is the order ``export_document`` builds,
    which is dependency order — the file reads top to bottom as "here is what this
    is, here is who is in it, here is what they own". Indented because the first
    thing anyone does with an export is open it.
    """
    return json.dumps(document, indent=2, ensure_ascii=False, default=str).encode("utf-8")

#: What a credential-less connection's ``last_error`` says. One definition, so the
#: panel, the log line and the test cannot describe the same state three ways.
CREDENTIAL_NOT_EXPORTED = (
    "The bank credential was not included in the export — it never leaves the "
    "instance. Reconnect this connection to resume syncing."
)

#: A decimal as the format writes it: an optional sign, digits, an optional
#: fraction. Deliberately not a float pattern — "1e5" and "1.2.3" are both
#: refused with a message naming the path rather than parsed into something.
_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


# ---- Scalars ----------------------------------------------------------------


def _num(value: Decimal) -> str:
    """A Decimal as a fixed-point string.

    ``"f"`` and not ``str()``: ``str(Decimal("1E+2"))`` is ``"1E+2"``, which is
    valid JSON to some readers and a nasty surprise to others, and a
    ``NUMERIC(19,4)`` column can come back in exponent form. ``"f"`` is always
    plain digits, and it preserves the scale the database actually stores, so the
    export is byte-faithful rather than re-rounded.
    """
    return format(value, "f")


def _export_value(value: Any) -> Any:
    """Convert one column value to its JSON form.

    A UUID becomes its string, a datetime or date its ISO-8601, a Decimal its
    fixed-point string. Anything else is passed through and will be caught by
    ``json.dumps`` if it cannot be represented — a failure at export time and
    loudly, rather than a silently dropped key.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _num(value)
    return value


def _row(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: _export_value(getattr(obj, f)) for f in fields}


# ---- Readers (import side) --------------------------------------------------


def _fail(where: str, problem: str) -> LedgerError:
    return LedgerError(f"{where}: {problem}", 400)


def _get(entry: dict[str, Any], key: str, where: str) -> Any:
    if key not in entry:
        raise _fail(where, f"missing required key {key!r}")
    return entry[key]


def _text(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> str | None:
    value = _get(entry, key, where) if required else entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(f"{where}.{key}", f"expected a string, got {type(value).__name__}")
    return value


def _money(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> Decimal | None:
    """Read one money (or quantity) cell.

    A JSON number is refused *by name*, which is the whole reason this is not
    ``Decimal(str(value))``: that spelling accepts ``1200.0`` and then has to
    decide whether the float that arrived was the number that was meant, and the
    one thing a ledger cannot do is decide that.
    """
    value = _get(entry, key, where) if required else entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _DECIMAL_RE.match(value):
        raise _fail(
            f"{where}.{key}",
            f'expected a decimal string like "1200.0000" (money is never a JSON '
            f"number — ADR-0005), got {value!r}",
        )
    return Decimal(value)


def _int(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> int | None:
    value = _get(entry, key, where) if required else entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{where}.{key}", f"expected an integer, got {value!r}")
    return value


def _bool(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> bool | None:
    value = _get(entry, key, where) if required else entry.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _fail(f"{where}.{key}", f"expected a boolean, got {value!r}")
    return value


def _str_id(raw: Any, where: str) -> uuid.UUID | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _fail(where, f"expected a uuid string, got {raw!r}")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise _fail(where, f"not a uuid: {raw!r}") from exc


def _dt(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> datetime | None:
    raw = _text(entry, key, where, required=required)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _fail(f"{where}.{key}", f"not an ISO datetime: {raw!r}") from exc
    if parsed.tzinfo is None:
        # Every timestamp in the schema is ``timezone=True`` and every writer
        # stores aware UTC; a naive one would be read back in whatever zone the
        # server happens to be in, which is a date shift waiting to happen.
        raise _fail(f"{where}.{key}", f"datetime has no timezone: {raw!r}")
    return parsed


def _date(entry: dict[str, Any], key: str, where: str, *, required: bool = True) -> date | None:
    raw = _text(entry, key, where, required=required)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise _fail(f"{where}.{key}", f"not an ISO date: {raw!r}") from exc


def _entries(doc: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = doc.get(key, [])
    if not isinstance(value, list):
        raise _fail(key, f"expected a list, got {type(value).__name__}")
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise _fail(f"{key}[{i}]", f"expected an object, got {type(entry).__name__}")
    return value


# ---- The export -------------------------------------------------------------

#: Column lists, written out rather than read off the model, so that adding a
#: column to a table cannot silently add it to the export. A new column deserves a
#: deliberate decision about whether it is household data — which is exactly the
#: question the credential column must never be allowed to answer by default.
_OWNER_FIELDS = ("id", "name", "kind", "sort")
_GROUP_FIELDS = ("id", "name", "type", "sort")
_CATEGORY_FIELDS = ("id", "group_id", "name", "icon", "color", "rollover", "sort")
_TAG_FIELDS = ("id", "name", "color")
# ``access_url_encrypted`` is not in this tuple and must never be.
_CONNECTION_FIELDS = ("id", "provider", "org_name", "display_name", "status",
                      "is_enabled", "sync_interval_minutes")
_ACCOUNT_FIELDS = (
    "id", "connection_id", "external_id", "external_key", "name", "type", "subtype",
    "institution", "currency", "current_balance", "available_balance", "balance_date",
    "balance_source", "is_asset", "owner_id", "is_manual", "is_hidden",
)
_SNAPSHOT_FIELDS = ("account_id", "balance_date", "balance", "currency")
_SECURITY_FIELDS = ("id", "name", "ticker", "security_type", "currency", "is_manual")
_PRICE_FIELDS = ("security_id", "price_date", "price", "currency", "source")
_HOLDING_FIELDS = ("account_id", "security_id", "quantity", "cost_basis", "as_of", "source")
_TRANSFER_GROUP_FIELDS = ("id", "matched_by", "fx_cost_base")
# ``base_amount`` and ``fx_rate_date`` are absent on purpose; see the module docstring.
# So is ``import_hash``: it is a digest of the *source* account's uuid, so in the
# target it is not a weaker version of the key, it is a different number. The import
# recomputes it. A field that cannot be right is a field that can only mislead.
_TRANSACTION_FIELDS = (
    "id", "account_id", "external_id", "posted_at", "transacted_at",
    "amount", "currency", "description", "merchant", "category_id", "owner_id",
    "is_pending", "pending_since", "review_status", "is_hidden", "is_split_parent",
    "transfer_group_id", "field_sources", "notes", "source",
)
_SPLIT_FIELDS = ("id", "parent_txn_id", "amount", "category_id", "owner_id", "notes")
_RULE_FIELDS = ("id", "priority", "name", "enabled", "conditions", "actions")
_INVESTMENT_TX_FIELDS = (
    "id", "account_id", "security_id", "type", "external_id",
    "trade_date", "quantity", "price", "amount", "currency", "transfer_group_id",
    "field_sources", "description", "notes", "source",
)
_RATE_FIELDS = ("base_currency", "quote_currency", "rate_date", "rate")
# ADR-0052: foundation-only, nothing else references these yet.
_INCOME_PROFILE_FIELDS = (
    "id", "owner_id", "currency", "annual_gross_income", "pay_frequency",
    "filing_status", "tax_region",
)
_PAYSTUB_FIELDS = (
    "id", "owner_id", "pay_date", "period_start", "period_end", "employer",
    "currency", "gross", "net",
)
_PAYSTUB_LINE_FIELDS = (
    "id", "paystub_id", "kind", "label", "amount", "ytd_amount", "position",
)
# ADR-0053. ``transaction_id`` travels because it is provenance a person can see
# ("picked from this charge"); the import remaps it optionally, since the
# transaction it names may be one this document did not carry.
_RECURRING_FIELDS = (
    "id", "name", "merchant", "account_id", "category_id", "amount", "currency",
    "cadence", "next_due_date", "is_active", "transaction_id",
)


async def _select(session: AsyncSession, model: type, order_by=None) -> list[Any]:
    stmt = select(model)
    if order_by is not None:
        stmt = stmt.order_by(*order_by)
    return list((await session.execute(stmt)).scalars().all())


async def export_document(session: AsyncSession, household_id: uuid.UUID) -> dict[str, Any]:
    """The household as one re-importable document.

    Everything the household owns, in dependency order, plus the FX rates its
    conversions depend on — and nothing operational (users, sessions, sync jobs,
    runs and events) and no credential.
    """
    household = (
        await session.execute(select(Household).where(Household.id == household_id))
    ).scalar_one()

    connections = await _select(session, AccountConnection, (AccountConnection.id,))
    owners = await _select(session, Owner, (Owner.sort, Owner.id))
    groups = await _select(session, CategoryGroup, (CategoryGroup.sort, CategoryGroup.id))
    categories = await _select(session, Category, (Category.sort, Category.id))
    tags = await _select(session, Tag, (Tag.name,))
    accounts = await _select(session, Account, (Account.name, Account.id))
    snapshots = await _select(
        session, BalanceSnapshot, (BalanceSnapshot.account_id, BalanceSnapshot.balance_date)
    )
    securities = await _select(session, Security, (Security.name, Security.id))
    prices = await _select(
        session, SecurityPrice, (SecurityPrice.security_id, SecurityPrice.price_date)
    )
    holdings = await _select(session, Holding, (Holding.account_id, Holding.security_id))
    transfer_groups = await _select(session, TransferGroup, (TransferGroup.id,))
    # Deterministic order, and not for tidiness: the import derives a manual row's
    # ``import_hash`` from its *ordinal among identical rows in this document*, so
    # two exports of the same unchanged household must list those rows in the same
    # order or a re-import would compute different digests and duplicate them.
    transactions = await _select(
        session, Transaction, (Transaction.account_id, Transaction.transacted_at, Transaction.id)
    )
    splits = await _select(session, TransactionSplit, (TransactionSplit.id,))
    links = (
        await session.execute(
            select(TransactionTag.transaction_id, TransactionTag.tag_id).order_by(
                TransactionTag.transaction_id, TransactionTag.tag_id
            )
        )
    ).all()
    investment_txns = await _select(
        session, InvestmentTransaction,
        (InvestmentTransaction.account_id, InvestmentTransaction.trade_date,
         InvestmentTransaction.id),
    )
    rules = await _select(session, Rule, (Rule.priority, Rule.id))
    rates = await _select(
        session, FxRate, (FxRate.base_currency, FxRate.quote_currency, FxRate.rate_date)
    )
    income_profiles = await _select(
        session, OwnerIncomeProfile, (OwnerIncomeProfile.owner_id,)
    )
    paystubs = await _select(session, Paystub, (Paystub.owner_id, Paystub.pay_date, Paystub.id))
    paystub_lines = await _select(
        session, PaystubLine, (PaystubLine.paystub_id, PaystubLine.position)
    )
    recurring = await _select(
        session, RecurringSeries, (RecurringSeries.name, RecurringSeries.id)
    )

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        # base_currency and timezone travel because every number below is
        # *interpreted* through them. The name does not: an import lands in a
        # household people are already using, and renaming it is not this
        # document's business.
        "household": {
            "base_currency": household.base_currency,
            "timezone": household.timezone,
        },
        "connections": [_row(c, _CONNECTION_FIELDS) for c in connections],
        "owners": [_row(o, _OWNER_FIELDS) for o in owners],
        "category_groups": [_row(g, _GROUP_FIELDS) for g in groups],
        "categories": [_row(c, _CATEGORY_FIELDS) for c in categories],
        "tags": [_row(t, _TAG_FIELDS) for t in tags],
        "accounts": [_row(a, _ACCOUNT_FIELDS) for a in accounts],
        "balance_snapshots": [_row(s, _SNAPSHOT_FIELDS) for s in snapshots],
        "securities": [_row(s, _SECURITY_FIELDS) for s in securities],
        "security_prices": [_row(p, _PRICE_FIELDS) for p in prices],
        "holdings": [_row(h, _HOLDING_FIELDS) for h in holdings],
        "transfer_groups": [_row(g, _TRANSFER_GROUP_FIELDS) for g in transfer_groups],
        "transactions": [_row(t, _TRANSACTION_FIELDS) for t in transactions],
        "transaction_splits": [_row(s, _SPLIT_FIELDS) for s in splits],
        "transaction_tags": [
            {"transaction_id": str(txn_id), "tag_id": str(tag_id)} for txn_id, tag_id in links
        ],
        "investment_transactions": [_row(t, _INVESTMENT_TX_FIELDS) for t in investment_txns],
        "rules": [_row(r, _RULE_FIELDS) for r in rules],
        "fx_rates": [_row(r, _RATE_FIELDS) for r in rates],
        "owner_income_profiles": [_row(p, _INCOME_PROFILE_FIELDS) for p in income_profiles],
        "paystubs": [_row(p, _PAYSTUB_FIELDS) for p in paystubs],
        "paystub_lines": [_row(pl, _PAYSTUB_LINE_FIELDS) for pl in paystub_lines],
        "recurring_series": [_row(r, _RECURRING_FIELDS) for r in recurring],
    }


# ---- The CSV ----------------------------------------------------------------

#: The header the CSV export writes: every one of these is a key in
#: ``services.imports._SYNONYMS``, so the mapping dialog auto-suggests all six and
#: the file is importable without a human touching the mapping.
CSV_HEADERS = ("date", "amount", "description", "category", "owner", "notes")

#: A UTF-8 BOM, because the usual target of a CSV export is a spreadsheet and
#: Excel reads a BOM-less UTF-8 file as the local code page. Our own importer
#: decodes with ``utf-8-sig``, so the same file also round-trips through it.
CSV_BOM = "﻿"


def transactions_csv(transactions: list[Transaction], owners: dict[uuid.UUID, str],
                     categories: dict[uuid.UUID, str]) -> bytes:
    """One account's transactions as a spreadsheet.

    Dates are ISO, amounts are plain signed decimals, and category and owner are
    written by **name** because that is what the importer resolves — an id would
    be a foreign key into an instance the reader may not have.

    The dates are date-only, deliberately. ``transacted_at`` carries a time and
    the importer stores a bare date at noon UTC (``imports._transacted_at``), so a
    round trip through this file is lossy on the *time* and exact on everything
    else. That is the honest trade for a format a human reads: the JSON document
    is the lossless one, and this is the one you open in Numbers.

    The ``description`` column falls back to ``merchant``. A row typed into the app
    carries a merchant and *no* description — ``description`` is the raw provider
    string, so only a synced or imported row has one — and without the fallback
    every hand-entered row exports with a blank payee. The file would still
    balance, and would be a list of dates and amounts with nothing saying what any
    of them were for. The fallback can only fire on rows that are blank today, and
    it is the vocabulary this format already has: ``imports._SYNONYMS`` maps
    ``payee`` to the same column.
    """
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for txn in transactions:
        writer.writerow((
            txn.transacted_at.date().isoformat(),
            _num(txn.amount),
            txn.description or txn.merchant or "",
            categories.get(txn.category_id, "") if txn.category_id else "",
            owners.get(txn.owner_id, "") if txn.owner_id else "",
            txn.notes or "",
        ))
    return (CSV_BOM + out.getvalue()).encode("utf-8")


# ---- The import -------------------------------------------------------------


class ImportResult:
    """What an import did, per entity: created, matched (already there)."""

    def __init__(self) -> None:
        self.created: dict[str, int] = {}
        self.matched: dict[str, int] = {}
        self.warnings: list[str] = []

    def made(self, entity: str, n: int = 1) -> None:
        # A zero is not recorded, and the rule is here rather than at each call
        # site. "Created 0 fx_rates" is not a smaller truth than "created no
        # fx_rates" — it is a different one, and the difference is exactly what a
        # bulk insert with a conflict clause has to be careful about. An entity
        # that is absent from the map did not happen; nothing else is meant.
        if n > 0:
            self.created[entity] = self.created.get(entity, 0) + n

    def found(self, entity: str, n: int = 1) -> None:
        if n > 0:
            self.matched[entity] = self.matched.get(entity, 0) + n

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "matched": self.matched, "warnings": self.warnings}


class _Remap:
    """Old id → new id, with the two lookups the difference between them needs.

    ``get`` is for a reference the document must define — a transaction's account
    — and ``get_optional`` for one it may not, because a rule can point at a
    category the user has since deleted and ``None`` is a real answer there. The
    separate method is what keeps that judgement at the call site instead of
    hidden in a default.
    """

    def __init__(self, result: ImportResult) -> None:
        self._result = result
        self.ids: dict[uuid.UUID, uuid.UUID] = {}

    def put(self, old: uuid.UUID | None, new: uuid.UUID | None) -> None:
        if old is not None and new is not None:
            self.ids[old] = new

    def get(self, old: uuid.UUID | None, where: str, kind: str) -> uuid.UUID | None:
        """Resolve a reference that must have been defined already.

        Reaching here unresolved means the document named an entity after
        something that points at it, or left it out — a hand-edited or truncated
        file. Naming the dangling id is the whole value of the check: the
        alternative is a foreign-key error from Postgres that says nothing about
        which row to look at.
        """
        if old is None:
            return None
        try:
            return self.ids[old]
        except KeyError:
            raise _fail(
                where, f"references {kind} {old}, which this document does not define"
            ) from None

    def get_optional(self, old: uuid.UUID | None, kind: str) -> uuid.UUID | None:
        """Resolve a reference that may legitimately be gone, recording the drop."""
        if old is None:
            return None
        new = self.ids.get(old)
        if new is None:
            self._result.warnings.append(
                f"dropped a reference to {kind} {old}, which this document does not define"
            )
        return new


async def _by_name(session: AsyncSession, model: type) -> dict[str, Any]:
    """``lower(name) → row`` for a household-scoped naming table.

    The query runs in the request's household-scoped session, so a name that
    exists only in another household is simply not in the map: nothing here can
    match a foreign row, by construction rather than by a check somebody has to
    remember to write.
    """
    rows = (await session.execute(select(model))).scalars().all()
    out: dict[str, Any] = {}
    for row in rows:
        out.setdefault(row.name.strip().lower(), row)
    return out


async def _first(session: AsyncSession, model: type, *where) -> Any:
    return (await session.execute(select(model).where(*where))).scalars().first()


class _Ordinals:
    """The nth row claiming a key, matched to the nth such row that already exists.

    A key can be shared by rows a human would still call different: two rules both
    named "Coffee", two connections to the same bank. Counting occurrences keeps
    those apart — the first document entry takes the first existing row, the
    second creates a new one — where a plain dict would collapse them onto one row
    and silently lose the rest.

    It is the same convention ``imports.import_hash`` uses for identical manual
    rows, deliberately: "the same" should not mean one thing in the CSV reader and
    another in this one.
    """

    def __init__(self) -> None:
        self._seen: dict[Any, int] = {}

    def next(self, key: Any) -> int:
        count = self._seen.get(key, 0)
        self._seen[key] = count + 1
        return count


def _nth(rows_by_key: dict[Any, list[Any]], key: Any, ordinal: int) -> Any | None:
    rows = rows_by_key.get(key) or []
    return rows[ordinal] if ordinal < len(rows) else None


async def _adoptable(session: AsyncSession, model: type, *identity: Any,
                     ordinal: int) -> Any | None:
    """The ``ordinal``-th hand-typed row of this identity, if there is one.

    ``identity`` is the criteria themselves — ``Transaction.amount == amount`` and
    so on — rather than a mapping of names to values. A mapping reads better at the
    call site and is a trap: ``{"amount": amount}`` invites ``key == value``, where
    the key is the *string* ``"amount"``, and the whole clause silently evaluates to
    Python's ``False`` and matches nothing. Passing expressions makes the wrong
    thing unrepresentable. ``_first`` above takes the same shape.

    A row typed into the UI carries no ``import_hash``, and ``ledger`` never writes
    one — nothing about a person entering a row asserts that it is the same as any
    other row. A digest lookup therefore cannot see it, which would make importing
    your own export back into the household you exported it from duplicate every
    row you had typed by hand. That is the one restore path a user is most likely
    to take, so the digest is not the last word: identical rows without one can be
    adopted, by the same ordinal rule the digest itself is built on.

    Only rows with *neither* key are candidates. A row that has a digest is a
    file's row and is matched by that digest or not at all — its ordinal was
    decided by the file that created it, and re-deriving it from the account's
    current contents would pick the wrong row the moment something in between was
    deleted. ``external_id`` is excluded for the same reason at a coarser grain: a
    provider's row is the provider's, and a manual row that resembles it is not it.
    """
    rows = (
        await session.execute(
            select(model)
            .where(*identity, model.import_hash.is_(None), model.external_id.is_(None))
            .order_by(model.id)
        )
    ).scalars().all()
    return rows[ordinal] if ordinal < len(rows) else None


class _Legs:
    """What the imported rows wanted from each document transfer group.

    A transfer group is not a thing anyone names — it is the link between rows —
    so its identity comes from its legs and cannot be known until the rows are in.
    This is where the transaction passes record what they find: the rows they
    created that asked for a group, and, for a row they merely *matched* that
    already sits in a live group, that group. The second half is what keeps a
    half-imported transfer linked: the new leg joins the pair that is already here
    rather than becoming a transfer to nowhere.
    """

    def __init__(self) -> None:
        self.created: dict[uuid.UUID, list[Any]] = {}
        self.live: dict[uuid.UUID, uuid.UUID] = {}

    def created_row(self, old_group: uuid.UUID, row: Any) -> None:
        self.created.setdefault(old_group, []).append(row)

    def live_group(self, old_group: uuid.UUID, group_id: uuid.UUID | None) -> None:
        if group_id is not None:
            self.live.setdefault(old_group, group_id)


def _digest(account_id: uuid.UUID, when: datetime, amount: Decimal, description: str | None,
            ordinal: int) -> str:
    """The same digest ``services.imports.import_hash`` computes.

    Reimplemented rather than imported, because this module reads the *document's*
    ordinals and that one reads a CSV file's; the two must agree on the payload,
    not on their inputs, and a shared function would have to be told which kind of
    file it was looking at. ``test_portability.py`` pins them to each other with a
    case that fails the moment the payloads diverge.
    """
    payload = "|".join((
        str(account_id),
        when.isoformat(),
        format(quantize_storage(amount), "f"),
        description or "",
        str(ordinal),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _noon(on: date) -> datetime:
    """A date-only document means "this calendar day" everywhere, so it is stored
    as noon UTC — the ledger's one convention for it (``imports._transacted_at``),
    which is also what makes a manual row's digest stable across the two readers."""
    return datetime(on.year, on.month, on.day, 12, tzinfo=UTC)


async def import_document(session: AsyncSession, household_id: uuid.UUID,
                          raw: bytes) -> ImportResult:
    """Load a document into this household. Idempotent against natural keys.

    Runs wholly inside the caller's scoped transaction, so a document either
    lands whole or not at all; and because the session is household-scoped, RLS
    is what makes "this document cannot reach another household" true, rather
    than a check in here that a later edit could weaken.
    """
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"Not a JSON document: {exc.msg} (line {exc.lineno})", 400) from exc
    if not isinstance(doc, dict):
        raise LedgerError("Not an export document: expected a JSON object", 400)
    if doc.get("format") != FORMAT:
        raise LedgerError(
            f'Not a MetalMark export: expected "format": {FORMAT!r}, '
            f"got {doc.get('format')!r}",
            400,
        )
    if doc.get("version") not in READABLE_VERSIONS:
        # Refused, never partially applied. A newer document read by older code
        # would import every field the two versions share and silently drop the
        # rest, which is the failure a format version exists to make impossible.
        raise LedgerError(
            f"This export is version {doc.get('version')}; this instance reads version "
            f"{VERSION}. Upgrade the instance rather than importing it partially.",
            400,
        )
    if doc.get("version") == 1:
        doc = _upgrade_v1(doc)

    result = ImportResult()
    remap = _Remap(result)
    legs = _Legs()
    try:
        await _import_household_settings(session, household_id, doc)
        await _import_owners(session, household_id, doc, remap, result)
        await _import_category_groups(session, household_id, doc, remap, result)
        await _import_categories(session, household_id, doc, remap, result)
        await _import_tags(session, household_id, doc, remap, result)
        await _import_connections(session, household_id, doc, remap, result)
        await _import_accounts(session, household_id, doc, remap, result)
        await _import_securities(session, household_id, doc, remap, result)
        await _import_snapshots(session, household_id, doc, remap, result)
        await _import_prices(session, household_id, doc, remap, result)
        await _import_holdings(session, household_id, doc, remap, result)
        rows, created = await _import_transactions(session, household_id, doc, remap, result,
                                                   legs)
        # Into the remap as well as into the three importers that take the map
        # directly: a transaction is referenced by document id from outside its own
        # block — a recurring series names the charge it was picked from — so the
        # reference resolves like any other rather than warning "not defined" about
        # a row the document plainly carries.
        for old, new in rows.items():
            remap.put(old, new)
        await _import_splits(session, doc, remap, result, rows, created)
        await _import_transaction_tags(session, doc, remap, result, rows, created)
        await _import_investment_transactions(session, household_id, doc, remap, result, legs)
        # After the rows, and only just: a transfer group is created from the legs
        # that want it, so the rows have to be in before it can exist.
        await _import_transfer_groups(session, household_id, doc, remap, result, legs)
        await _import_rules(session, household_id, doc, remap, result)
        await _import_fx_rates(session, doc, result)
        await _import_income_profiles(session, household_id, doc, remap, result)
        pay_rows, pay_created = await _import_paystubs(session, household_id, doc, remap, result)
        await _import_paystub_lines(session, household_id, doc, pay_rows, pay_created, result)
        # Last, and after the transactions it may point at: a series references an
        # account, a category and (as provenance) one transaction, so every one of
        # those remaps has to be populated before this runs.
        await _import_recurring_series(session, household_id, doc, remap, result)
    except IntegrityError as exc:
        # Nothing here adopts a source id, so a primary-key collision is no longer
        # the expected shape of failure — it means the document contradicts itself
        # (two entries claiming one key) or a uniqueness rule this reader does not
        # implement has refused a row. Both are the document's problem, and the
        # message says so instead of blaming the household.
        raise LedgerError(
            "This document could not be imported: it contains rows that conflict "
            "with each other or with the household it is being imported into. No "
            "part of it was applied.",
            409,
        ) from exc
    return result


async def _import_household_settings(session: AsyncSession, household_id: uuid.UUID,
                                     doc: dict[str, Any]) -> None:
    """Apply the base currency and timezone.

    The base currency is not a preference: it is what every ``base_amount`` and
    every reported total is *in*. An import that left it behind would reinterpret
    every number in the document through the target's currency and call the
    result a restore.
    """
    block = doc.get("household", {})
    if not isinstance(block, dict):
        raise _fail("household", "expected an object")
    household = (
        await session.execute(select(Household).where(Household.id == household_id))
    ).scalar_one()
    base = _text(block, "base_currency", "household", required=False)
    if base is not None:
        if len(base) != 3:
            raise _fail("household.base_currency", f"expected a 3-letter code, got {base!r}")
        household.base_currency = base.upper()
    timezone = _text(block, "timezone", "household", required=False)
    if timezone is not None:
        household.timezone = timezone


async def _import_owners(session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any],
                         remap: _Remap, result: ImportResult) -> None:
    existing = await _by_name(session, Owner)
    for i, entry in enumerate(_entries(doc, "owners")):
        where = f"owners[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        name = _text(entry, "name", where) or ""
        found = existing.get(name.strip().lower())
        if found is not None:
            remap.put(old_id, found.id)
            result.found("owners")
            continue
        owner = Owner(
            household_id=household_id,
            name=name,
            kind=_text(entry, "kind", where) or "person",
            sort=_int(entry, "sort", where, required=False) or 0,
        )
        session.add(owner)
        await session.flush()
        existing[name.strip().lower()] = owner
        remap.put(old_id, owner.id)
        result.made("owners")


async def _import_category_groups(session: AsyncSession, household_id: uuid.UUID,
                                  doc: dict[str, Any], remap: _Remap,
                                  result: ImportResult) -> None:
    existing = await _by_name(session, CategoryGroup)
    for i, entry in enumerate(_entries(doc, "category_groups")):
        where = f"category_groups[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        name = _text(entry, "name", where) or ""
        found = existing.get(name.strip().lower())
        if found is not None:
            remap.put(old_id, found.id)
            result.found("category_groups")
            continue
        group = CategoryGroup(
            household_id=household_id,
            name=name,
            type=_text(entry, "type", where) or "expense",
            sort=_int(entry, "sort", where, required=False) or 0,
        )
        session.add(group)
        await session.flush()
        existing[name.strip().lower()] = group
        remap.put(old_id, group.id)
        result.made("category_groups")


async def _import_categories(session: AsyncSession, household_id: uuid.UUID,
                             doc: dict[str, Any], remap: _Remap,
                             result: ImportResult) -> None:
    """Categories key on their name **within their group**.

    Categories carry no unique constraint — two groups may each hold a
    "Groceries" — so the natural key has to be the pair, which is also the pair a
    human means when they say "the same category".
    """
    by_key = {
        (group_id, name.strip().lower()): cat_id
        for cat_id, group_id, name in (
            await session.execute(select(Category.id, Category.group_id, Category.name))
        ).all()
    }
    for i, entry in enumerate(_entries(doc, "categories")):
        where = f"categories[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        name = (_text(entry, "name", where) or "").strip()
        group_id = remap.get(
            _str_id(_get(entry, "group_id", where), f"{where}.group_id"), where, "category group"
        )
        if group_id is None:
            raise _fail(where, "a category must name its group")
        key = (group_id, name.lower())
        found = by_key.get(key)
        if found is not None:
            remap.put(old_id, found)
            result.found("categories")
            continue
        category = Category(
            household_id=household_id,
            group_id=group_id,
            name=name,
            icon=_text(entry, "icon", where, required=False),
            color=_text(entry, "color", where, required=False),
            rollover=_bool(entry, "rollover", where, required=False) or False,
            sort=_int(entry, "sort", where, required=False) or 0,
        )
        session.add(category)
        await session.flush()
        by_key[key] = category.id
        remap.put(old_id, category.id)
        result.made("categories")


async def _import_tags(session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any],
                       remap: _Remap, result: ImportResult) -> None:
    existing = await _by_name(session, Tag)
    for i, entry in enumerate(_entries(doc, "tags")):
        where = f"tags[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        name = _text(entry, "name", where) or ""
        found = existing.get(name.strip().lower())
        if found is not None:
            remap.put(old_id, found.id)
            result.found("tags")
            continue
        tag = Tag(household_id=household_id, name=name,
                  color=_text(entry, "color", where, required=False))
        session.add(tag)
        await session.flush()
        existing[name.strip().lower()] = tag
        remap.put(old_id, tag.id)
        result.made("tags")


async def _import_connections(session: AsyncSession, household_id: uuid.UUID,
                              doc: dict[str, Any], remap: _Remap,
                              result: ImportResult) -> None:
    """Connections land with no credential, so they land broken — honestly.

    Keyed on ``(provider, org_name)``, which is the only thing about a connection a
    person can name — its id is a uuid nobody chose, and its accounts are the
    *consequence* of it rather than its key. Two logins to one bank are two
    connections, which the ordinal rule keeps apart.

    The cost is real and worth stating: a household that already holds two claims
    to one institution and imports a document holding one cannot say which it
    means, and gets the first. The alternative was worse. A connection is the row a
    user must *reconnect*, so a duplicate is not a harmless extra row — it is a
    second "paste your token" prompt for an institution already connected, arriving
    afresh on every import.
    """
    existing = (await session.execute(select(AccountConnection))).scalars().all()
    by_name: dict[Any, list[AccountConnection]] = {}
    for connection in existing:
        by_name.setdefault((connection.provider, connection.org_name), []).append(connection)
    ordinals = _Ordinals()
    for i, entry in enumerate(_entries(doc, "connections")):
        where = f"connections[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "a connection must carry its id")
        provider = _text(entry, "provider", where) or "simplefin"
        key = (provider, _text(entry, "org_name", where, required=False))
        found = _nth(by_name, key, ordinals.next(key))
        if found is not None:
            # Its credential may be on this instance already; an import must not
            # blank it, and must not resurrect a status the live connection has
            # since moved past.
            remap.put(old_id, found.id)
            result.found("connections")
            continue
        connection = AccountConnection(
            household_id=household_id,
            provider=provider,
            access_url_encrypted=None,
            org_name=_text(entry, "org_name", where, required=False),
            # Absent in an older file (no ``display_name`` column existed yet at
            # export time) reads the same as a present-but-null field: NULL,
            # meaning "use the bank's name" — which is correct either way.
            display_name=_text(entry, "display_name", where, required=False),
            status="auth_error",
            last_error=CREDENTIAL_NOT_EXPORTED,
            is_enabled=_bool(entry, "is_enabled", where, required=False) is not False,
            sync_interval_minutes=(
                _int(entry, "sync_interval_minutes", where, required=False) or 360
            ),
        )
        session.add(connection)
        await session.flush()
        by_name.setdefault(key, []).append(connection)
        remap.put(old_id, connection.id)
        result.made("connections")


def _upgrade_v1(doc: dict[str, Any]) -> dict[str, Any]:
    """A version-1 document with its liability balances in ADR-0043's convention.

    Version 1 was written while a hand-entered card stored the amount owed as a
    positive number. ``balance_sign`` decides, per account, which positive balances
    were that — the same rule migration 0007 applied to the database — and those
    are negated here, before anything is imported, so the importer itself reads one
    convention. A cell that is not a readable number is left for the importer to
    refuse by name.
    """
    accounts = doc.get("accounts")
    snapshots = doc.get("balance_snapshots")
    if not isinstance(accounts, list):
        return doc
    snapshots = snapshots if isinstance(snapshots, list) else []

    def number(cell: Any) -> Decimal | None:
        try:
            return Decimal(cell) if isinstance(cell, str) else None
        except ArithmeticError:
            return None

    by_account: dict[Any, list[dict[str, Any]]] = {}
    for snap in snapshots:
        if isinstance(snap, dict):
            by_account.setdefault(snap.get("account_id"), []).append(snap)

    upgraded_accounts = []
    upgraded_snapshots = {id(s): s for s in snapshots}
    for account in accounts:
        if not isinstance(account, dict) or account.get("type") not in LIABILITY_TYPES:
            upgraded_accounts.append(account)
            continue
        rows = by_account.get(account.get("id"), [])
        cells = [account.get("current_balance"), *(r.get("balance") for r in rows)]
        values = [v for v in map(number, cells) if v is not None]
        if not positives_are_amounts_owed(values, ever_synced=bool(account.get("external_key"))):
            upgraded_accounts.append(account)
            continue

        def negated(cell: Any) -> Any:
            value = number(cell)
            return str(-value) if value is not None and value > 0 else cell

        upgraded_accounts.append(
            {**account, "current_balance": negated(account.get("current_balance"))}
        )
        for row in rows:
            upgraded_snapshots[id(row)] = {**row, "balance": negated(row.get("balance"))}
    return {
        **doc,
        "accounts": upgraded_accounts,
        "balance_snapshots": [upgraded_snapshots[id(s)] for s in snapshots],
    }


async def _import_accounts(session: AsyncSession, household_id: uuid.UUID,
                           doc: dict[str, Any], remap: _Remap, result: ImportResult) -> None:
    """Accounts key on ``external_key`` when they have one, else on their name.

    ``external_key`` is ADR-0009's reconnect key and the only stable identity a
    synced account has. A manual account has none and never will, so its identity
    is what a human would call it: name, type and currency. Two manual accounts
    sharing all three are not two accounts anyone meant to have.

    A matched account keeps the ``current_balance`` it has here rather than the
    document's. That is the merge case, not the restore case: an account the
    household has been syncing since the export has a *newer* balance, and the
    document's would be a regression.
    """
    rows = (await session.execute(select(Account))).scalars().all()
    by_key = {a.external_key: a for a in rows if a.external_key}
    by_name = {(a.name.strip().lower(), a.type, a.currency): a for a in rows}
    for i, entry in enumerate(_entries(doc, "accounts")):
        where = f"accounts[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        name = _text(entry, "name", where) or ""
        external_key = _text(entry, "external_key", where, required=False)
        currency = _text(entry, "currency", where) or "USD"
        type_ = _text(entry, "type", where) or "other"
        found = by_key.get(external_key) if external_key else None
        if found is None and not external_key:
            found = by_name.get((name.strip().lower(), type_, currency))
        if found is not None:
            remap.put(old_id, found.id)
            result.found("accounts")
            continue
        # The owner is the one reference that is genuinely optional: everywhere
        # else a dangling id is a broken document, but an account can always fall
        # back to this household's Shared owner (ADR-0026), which is exactly what
        # "unset" means — and "unset" includes the key being absent, because a
        # hand-written document that means Shared has no reason to say so.
        owner_id = remap.get_optional(
            _str_id(entry.get("owner_id"), f"{where}.owner_id"), "owner")
        if owner_id is None:
            owner_id = (await _shared_owner(session, household_id)).id
        account = Account(
            household_id=household_id,
            connection_id=remap.get_optional(
                _str_id(entry.get("connection_id"), f"{where}.connection_id"), "connection"),
            external_id=_text(entry, "external_id", where, required=False),
            external_key=external_key,
            name=name,
            type=type_,
            subtype=_text(entry, "subtype", where, required=False),
            institution=_text(entry, "institution", where, required=False),
            currency=currency,
            current_balance=_money(entry, "current_balance", where) or Decimal("0"),
            available_balance=_money(entry, "available_balance", where, required=False),
            balance_date=_date(entry, "balance_date", where, required=False),
            balance_source=_text(entry, "balance_source", where, required=False),
            is_asset=_bool(entry, "is_asset", where) or False,
            owner_id=owner_id,
            is_manual=_bool(entry, "is_manual", where, required=False) is not False,
            is_hidden=_bool(entry, "is_hidden", where, required=False) or False,
        )
        session.add(account)
        await session.flush()
        by_name[(name.strip().lower(), type_, currency)] = account
        if external_key:
            by_key[external_key] = account
        remap.put(old_id, account.id)
        result.made("accounts")


async def _shared_owner(session: AsyncSession, household_id: uuid.UUID) -> Owner:
    from app.services.owners import ensure_shared_owner

    return await ensure_shared_owner(session, household_id)


async def _import_securities(session: AsyncSession, household_id: uuid.UUID,
                             doc: dict[str, Any], remap: _Remap,
                             result: ImportResult) -> None:
    """Securities key on ``(ticker, currency)`` when a ticker exists, else on name.

    That mirrors the model's own uniqueness, which treats cash — no ticker — as
    many-rows-allowed, so a household holding two currencies of cash keeps both.
    """
    rows = (await session.execute(select(Security))).scalars().all()
    by_ticker = {(s.ticker, s.currency): s for s in rows if s.ticker}
    by_name = {(s.name.strip().lower(), s.currency): s for s in rows if not s.ticker}
    for i, entry in enumerate(_entries(doc, "securities")):
        where = f"securities[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        name = _text(entry, "name", where) or ""
        ticker = _text(entry, "ticker", where, required=False)
        currency = _text(entry, "currency", where) or "USD"
        found = (by_ticker.get((ticker, currency)) if ticker
                 else by_name.get((name.strip().lower(), currency)))
        if found is not None:
            remap.put(old_id, found.id)
            result.found("securities")
            continue
        security = Security(
            household_id=household_id,
            name=name,
            ticker=ticker,
            security_type=_text(entry, "security_type", where) or "stock",
            currency=currency,
            is_manual=_bool(entry, "is_manual", where, required=False) is not False,
        )
        session.add(security)
        await session.flush()
        if ticker:
            by_ticker[(ticker, currency)] = security
        else:
            by_name[(name.strip().lower(), currency)] = security
        remap.put(old_id, security.id)
        result.made("securities")


async def _import_snapshots(session: AsyncSession, household_id: uuid.UUID,
                            doc: dict[str, Any], remap: _Remap,
                            result: ImportResult) -> None:
    """Keyed ``(account, balance_date)`` — the model's own unique constraint, and
    the identity a snapshot has."""
    for i, entry in enumerate(_entries(doc, "balance_snapshots")):
        where = f"balance_snapshots[{i}]"
        account_id = remap.get(_str_id(_get(entry, "account_id", where),
                                       f"{where}.account_id"), where, "account")
        balance_date = _date(entry, "balance_date", where)
        found = await _first(session, BalanceSnapshot,
                             BalanceSnapshot.account_id == account_id,
                             BalanceSnapshot.balance_date == balance_date)
        if found is not None:
            result.found("balance_snapshots")
            continue
        session.add(BalanceSnapshot(
            household_id=household_id,
            account_id=account_id,
            balance_date=balance_date,
            balance=_money(entry, "balance", where),
            currency=_text(entry, "currency", where) or "USD",
        ))
        await session.flush()
        result.made("balance_snapshots")


async def _import_prices(session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any],
                         remap: _Remap, result: ImportResult) -> None:
    for i, entry in enumerate(_entries(doc, "security_prices")):
        where = f"security_prices[{i}]"
        security_id = remap.get(_str_id(_get(entry, "security_id", where),
                                        f"{where}.security_id"), where, "security")
        price_date = _date(entry, "price_date", where)
        found = await _first(session, SecurityPrice,
                             SecurityPrice.security_id == security_id,
                             SecurityPrice.price_date == price_date)
        if found is not None:
            result.found("security_prices")
            continue
        session.add(SecurityPrice(
            household_id=household_id,
            security_id=security_id,
            price_date=price_date,
            price=_money(entry, "price", where),
            currency=_text(entry, "currency", where) or "USD",
            source=_text(entry, "source", where) or "manual",
        ))
        await session.flush()
        result.made("security_prices")


async def _import_holdings(session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any],
                           remap: _Remap, result: ImportResult) -> None:
    for i, entry in enumerate(_entries(doc, "holdings")):
        where = f"holdings[{i}]"
        account_id = remap.get(_str_id(_get(entry, "account_id", where),
                                       f"{where}.account_id"), where, "account")
        security_id = remap.get(_str_id(_get(entry, "security_id", where),
                                        f"{where}.security_id"), where, "security")
        found = await _first(session, Holding, Holding.account_id == account_id,
                             Holding.security_id == security_id)
        if found is not None:
            result.found("holdings")
            continue
        session.add(Holding(
            household_id=household_id,
            account_id=account_id,
            security_id=security_id,
            quantity=_money(entry, "quantity", where),
            cost_basis=_money(entry, "cost_basis", where, required=False),
            as_of=_date(entry, "as_of", where, required=False),
            source=_holding_source(entry, where),
        ))
        await session.flush()
        result.made("holdings")


def _holding_source(entry: dict[str, Any], where: str) -> str:
    """``manual`` when absent: a file written before ADR-0051 has only those."""
    source = _text(entry, "source", where, required=False) or "manual"
    if source not in HOLDING_SOURCES:
        raise _fail(f"{where}.source", f"expected one of {', '.join(HOLDING_SOURCES)}")
    return source


async def _import_transfer_groups(session: AsyncSession, household_id: uuid.UUID,
                                  doc: dict[str, Any], remap: _Remap, result: ImportResult,
                                  legs: _Legs) -> None:
    """Create a group for each one that actually links something here.

    Runs *after* the rows, because that is the only time an answer exists. A group
    whose legs were all matched is a link this household already has, so this
    document's id for it names nothing to do and no row is written — which is what
    makes the second import of a document create nothing. A group with no legs at
    all is dropped: it links nothing, here or there.
    """
    defined = set()
    for i, entry in enumerate(_entries(doc, "transfer_groups")):
        where = f"transfer_groups[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "a transfer group must carry its id")
        defined.add(old_id)

        rows = legs.created.get(old_id)
        if not rows:
            result.found("transfer_groups")
            continue

        group_id = legs.live.get(old_id)
        if group_id is None:
            group = TransferGroup(
                household_id=household_id,
                matched_by=_text(entry, "matched_by", where) or "manual",
                fx_cost_base=_money(entry, "fx_cost_base", where, required=False),
            )
            session.add(group)
            await session.flush()
            group_id = group.id
            result.made("transfer_groups")
        else:
            result.found("transfer_groups")
        remap.put(old_id, group_id)
        for row in rows:
            row.transfer_group_id = group_id

    for missing in sorted(legs.created.keys() - defined, key=str):
        result.warnings.append(
            f"a row named transfer group {missing}, which this document does not "
            f"define; the row was imported unlinked"
        )


def _remap_rule_blob(blob: Any, remap: _Remap, where: str) -> dict[str, Any]:
    """Rewrite the ids inside a rule's JSONB.

    A rule stores its references as uuids *inside* two JSON blobs (ARCHITECTURE
    §2), so the remap cannot be left to the foreign keys — there are none. The key
    set is closed and owned by ``schemas.rules``; this walks exactly those keys and
    leaves everything else (an amount, a regex, a merchant name) untouched, so an
    id-shaped string inside a ``description_regex`` is never rewritten by accident.
    """
    if not isinstance(blob, dict):
        raise _fail(where, f"expected an object, got {type(blob).__name__}")
    out = dict(blob)
    for key, kind in (("category_id", "category"), ("set_category_id", "category"),
                      ("set_owner_id", "owner")):
        if key in out:
            out[key] = _str_or_none(remap.get_optional(
                _str_id(out[key], f"{where}.{key}"), kind))
    for key, kind in (("account_ids", "account"), ("add_tag_ids", "tag")):
        if key in out:
            raw = out[key]
            if not isinstance(raw, list):
                raise _fail(f"{where}.{key}", f"expected a list, got {type(raw).__name__}")
            out[key] = [
                str(new_id)
                for new_id in (
                    remap.get_optional(_str_id(item, f"{where}.{key}"), kind) for item in raw
                )
                if new_id is not None
            ]
    if isinstance(out.get("split"), list):
        legs = []
        for j, leg in enumerate(out["split"]):
            if not isinstance(leg, dict):
                raise _fail(f"{where}.split[{j}]", "expected an object")
            leg = dict(leg)
            for key, kind in (("category_id", "category"), ("owner_id", "owner")):
                if key in leg:
                    leg[key] = _str_or_none(remap.get_optional(
                        _str_id(leg[key], f"{where}.split[{j}].{key}"), kind))
            legs.append(leg)
        out["split"] = legs
    return out


def _str_or_none(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


async def _import_transactions(session: AsyncSession, household_id: uuid.UUID,
                               doc: dict[str, Any], remap: _Remap, result: ImportResult,
                               legs: _Legs) -> tuple[dict[uuid.UUID, uuid.UUID],
                                                     set[uuid.UUID]]:
    """Insert transactions. Returns ``old id → new id`` and the ids it created.

    Dedupe is the ledger's existing two-key rule, unchanged: a row with an
    ``external_id`` is the provider's row and is keyed on ``(account,
    external_id)``; a row without one is keyed on ``import_hash``, which is
    *recomputed here* from the target account and this row's ordinal among
    identical rows in this document. Recomputing rather than trusting the exported
    digest is not caution — the exported hash is keyed to the source account's
    uuid, so it is meaningless in the target instance, and importing it would make
    every manual row a fresh insert on every import.

    New rows get a **fresh** id rather than the document's. Nothing needs the old
    one: the children follow through the remap, and ``field_sources`` holds no
    ids. Handing the database an id the document chose is how a second import into
    another household turns into a primary-key collision.
    """
    rows = (await session.execute(
        select(Transaction.account_id, Transaction.external_id, Transaction.id,
               Transaction.transfer_group_id))).all()
    by_external = {(account_id, external): (txn_id, group_id)
                   for account_id, external, txn_id, group_id in rows if external}
    ordinals: dict[tuple, int] = {}
    out: dict[uuid.UUID, uuid.UUID] = {}
    created: set[uuid.UUID] = set()

    for i, entry in enumerate(_entries(doc, "transactions")):
        where = f"transactions[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "a transaction must carry its id")
        account_id = remap.get(_str_id(_get(entry, "account_id", where),
                                       f"{where}.account_id"), where, "account")
        transacted_at = _dt(entry, "transacted_at", where)
        amount = _money(entry, "amount", where)
        description = _text(entry, "description", where, required=False)
        external_id = _text(entry, "external_id", where, required=False)
        # Not resolved here. A transfer group is created from its legs, so the
        # reference is parked on the row and fulfilled once every row is in.
        group_ref = _str_id(_get(entry, "transfer_group_id", where),
                            f"{where}.transfer_group_id")

        import_hash = None
        if external_id:
            found = by_external.get((account_id, external_id))
            if found is not None:
                out[old_id] = found[0]
                legs.live_group(group_ref, found[1])
                result.found("transactions")
                continue
        else:
            key = (account_id, transacted_at, quantize_storage(amount), description or "")
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            import_hash = _digest(account_id, transacted_at, amount, description, ordinal)
            existing = (
                await session.execute(
                    select(Transaction.id, Transaction.transfer_group_id).where(
                        Transaction.account_id == account_id,
                        Transaction.import_hash == import_hash,
                    )
                )
            ).first()
            if existing is None:
                adopted = await _adoptable(
                    session, Transaction,
                    Transaction.account_id == account_id,
                    Transaction.transacted_at == transacted_at,
                    Transaction.amount == quantize_storage(amount),
                    Transaction.description == description,
                    ordinal=ordinal,
                )
                if adopted is not None:
                    out[old_id] = adopted.id
                    legs.live_group(group_ref, adopted.transfer_group_id)
                    result.found("transactions")
                    continue
            else:
                out[old_id] = existing[0]
                legs.live_group(group_ref, existing[1])
                result.found("transactions")
                continue

        txn = Transaction(
            household_id=household_id,
            account_id=account_id,
            external_id=external_id,
            import_hash=import_hash,
            posted_at=_dt(entry, "posted_at", where, required=False),
            transacted_at=transacted_at,
            amount=amount,
            currency=_text(entry, "currency", where) or "USD",
            description=description,
            merchant=_text(entry, "merchant", where, required=False),
            category_id=remap.get_optional(
                _str_id(_get(entry, "category_id", where), f"{where}.category_id"), "category"),
            owner_id=remap.get_optional(
                _str_id(_get(entry, "owner_id", where), f"{where}.owner_id"), "owner"),
            is_pending=_bool(entry, "is_pending", where, required=False) or False,
            pending_since=_dt(entry, "pending_since", where, required=False),
            review_status=_text(entry, "review_status", where) or "needs_review",
            is_hidden=_bool(entry, "is_hidden", where, required=False) or False,
            transfer_group_id=None,
            field_sources=_get(entry, "field_sources", where) or {},
            notes=_text(entry, "notes", where, required=False),
            source=_text(entry, "source", where) or "manual",
        )
        session.add(txn)
        await session.flush()
        if external_id:
            by_external[(account_id, external_id)] = (txn.id, None)
        if group_ref is not None:
            legs.created_row(group_ref, txn)
        out[old_id] = txn.id
        created.add(old_id)
        result.made("transactions")

    await _fill_base_amounts(session, household_id, created, out)
    return out, created


async def _fill_base_amounts(session: AsyncSession, household_id: uuid.UUID,
                             created: set[uuid.UUID],
                             out: dict[uuid.UUID, uuid.UUID]) -> None:
    """Fill ``base_amount`` / ``fx_rate_date`` for the rows just inserted.

    Deliberately not exported (ADR-0036): the column is a cache of ``amount`` at a
    dated rate, so the document carries the *rates* and this recomputes the cache
    — which is what makes an exported total reproduce exactly rather than
    depending on whatever rates the target instance happened to hold.

    Only the rows this import created, so the other half of "idempotent" holds:
    an import that matched everything must write nothing at all.
    """
    if not created:
        return
    household = (
        await session.execute(select(Household).where(Household.id == household_id))
    ).scalar_one()
    ids = [out[old] for old in created]
    rows = (
        await session.execute(select(Transaction).where(Transaction.id.in_(ids)))
    ).scalars().all()
    conv = await fx.converter(
        session,
        base_ccy=household.base_currency,
        currencies={t.currency for t in rows},
        until=max(t.transacted_at for t in rows).date(),
    )
    for txn in rows:
        txn.base_amount, txn.fx_rate_date = await conv.to_base(
            amount=txn.amount,
            currency=txn.currency,
            on=txn.transacted_at.date(),
            base_ccy=household.base_currency,
        )


async def _import_splits(session: AsyncSession, doc: dict[str, Any], remap: _Remap,
                         result: ImportResult, rows: dict[uuid.UUID, uuid.UUID],
                         created: set[uuid.UUID]) -> None:
    """Splits, but only for the transactions this import actually created.

    A skipped parent already has its splits — they came in with it — so copying
    them again would either duplicate the legs or, worse, silently rewrite a
    human's later edit to them back to what the document remembered.
    """
    for i, entry in enumerate(_entries(doc, "transaction_splits")):
        where = f"transaction_splits[{i}]"
        old_parent = _str_id(_get(entry, "parent_txn_id", where), f"{where}.parent_txn_id")
        if old_parent not in created:
            continue
        session.add(TransactionSplit(
            parent_txn_id=rows[old_parent],
            amount=_money(entry, "amount", where),
            category_id=remap.get_optional(
                _str_id(_get(entry, "category_id", where), f"{where}.category_id"), "category"),
            owner_id=remap.get_optional(
                _str_id(_get(entry, "owner_id", where), f"{where}.owner_id"), "owner"),
            notes=_text(entry, "notes", where, required=False),
        ))
        result.made("transaction_splits")
        # The flag follows the legs, so a row with splits is a split parent by
        # construction rather than by the document having remembered to say so.
        parent = (
            await session.execute(select(Transaction).where(Transaction.id == rows[old_parent]))
        ).scalar_one()
        parent.is_split_parent = True
    await session.flush()


async def _import_transaction_tags(session: AsyncSession, doc: dict[str, Any], remap: _Remap,
                                   result: ImportResult, rows: dict[uuid.UUID, uuid.UUID],
                                   created: set[uuid.UUID]) -> None:
    for i, entry in enumerate(_entries(doc, "transaction_tags")):
        where = f"transaction_tags[{i}]"
        old_txn = _str_id(_get(entry, "transaction_id", where), f"{where}.transaction_id")
        if old_txn not in created:
            continue
        tag_id = remap.get(_str_id(_get(entry, "tag_id", where), f"{where}.tag_id"), where, "tag")
        session.add(TransactionTag(transaction_id=rows[old_txn], tag_id=tag_id))
        result.made("transaction_tags")
    await session.flush()


async def _import_investment_transactions(session: AsyncSession, household_id: uuid.UUID,
                                          doc: dict[str, Any], remap: _Remap,
                                          result: ImportResult, legs: _Legs) -> None:
    """Investment events, deduped by the same two-key rule as transactions.

    They carry the same ``external_id`` / ``import_hash`` pair and the same two
    partial unique indexes, so the rule is the model's, not this function's. They
    can also be a leg of a transfer group — a transfer in kind has two investment
    transactions rather than two cash rows — so they feed the same ``legs``.
    """
    rows = (await session.execute(
        select(InvestmentTransaction.account_id, InvestmentTransaction.external_id,
               InvestmentTransaction.id, InvestmentTransaction.transfer_group_id))).all()
    by_external = {(account_id, external): (txn_id, group_id)
                   for account_id, external, txn_id, group_id in rows if external}
    ordinals: dict[tuple, int] = {}
    inserted: list[uuid.UUID] = []

    for i, entry in enumerate(_entries(doc, "investment_transactions")):
        where = f"investment_transactions[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "an investment transaction must carry its id")
        account_id = remap.get(_str_id(_get(entry, "account_id", where),
                                       f"{where}.account_id"), where, "account")
        trade_date = _date(entry, "trade_date", where)
        amount = _money(entry, "amount", where)
        external_id = _text(entry, "external_id", where, required=False)
        description = _text(entry, "description", where, required=False)
        group_ref = _str_id(_get(entry, "transfer_group_id", where),
                            f"{where}.transfer_group_id")

        import_hash = None
        if external_id:
            found = by_external.get((account_id, external_id))
            if found is not None:
                legs.live_group(group_ref, found[1])
                result.found("investment_transactions")
                continue
        else:
            # A trade has a date and no time, so the digest is fed the same noon
            # UTC every other date-only writer uses — one convention for "this
            # calendar day" across the whole ledger.
            key = (account_id, trade_date, quantize_storage(amount), description or "",
                   _text(entry, "type", where))
            ordinal = ordinals.get(key, 0)
            ordinals[key] = ordinal + 1
            import_hash = _digest(account_id, _noon(trade_date), amount, description, ordinal)
            existing = (
                await session.execute(
                    select(InvestmentTransaction.id, InvestmentTransaction.transfer_group_id)
                    .where(
                        InvestmentTransaction.account_id == account_id,
                        InvestmentTransaction.import_hash == import_hash,
                    )
                )
            ).first()
            if existing is None:
                adopted = await _adoptable(
                    session, InvestmentTransaction,
                    InvestmentTransaction.account_id == account_id,
                    InvestmentTransaction.trade_date == trade_date,
                    InvestmentTransaction.amount == quantize_storage(amount),
                    InvestmentTransaction.description == description,
                    ordinal=ordinal,
                )
                if adopted is not None:
                    legs.live_group(group_ref, adopted.transfer_group_id)
                    result.found("investment_transactions")
                    continue
            else:
                legs.live_group(group_ref, existing[1])
                result.found("investment_transactions")
                continue

        txn = InvestmentTransaction(
            household_id=household_id,
            account_id=account_id,
            security_id=remap.get_optional(
                _str_id(_get(entry, "security_id", where), f"{where}.security_id"), "security"),
            type=_text(entry, "type", where) or "buy",
            external_id=external_id,
            import_hash=import_hash,
            trade_date=trade_date,
            quantity=_money(entry, "quantity", where, required=False),
            price=_money(entry, "price", where, required=False),
            amount=amount,
            currency=_text(entry, "currency", where) or "USD",
            transfer_group_id=None,
            field_sources=_get(entry, "field_sources", where) or {},
            description=description,
            notes=_text(entry, "notes", where, required=False),
            source=_text(entry, "source", where) or "manual",
        )
        session.add(txn)
        await session.flush()
        if external_id:
            by_external[(account_id, external_id)] = (txn.id, None)
        if group_ref is not None:
            legs.created_row(group_ref, txn)
        inserted.append(txn.id)
        result.made("investment_transactions")

    await _fill_investment_base_amounts(session, household_id, inserted)


async def _fill_investment_base_amounts(session: AsyncSession, household_id: uuid.UUID,
                                        ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    household = (
        await session.execute(select(Household).where(Household.id == household_id))
    ).scalar_one()
    rows = (
        await session.execute(
            select(InvestmentTransaction).where(InvestmentTransaction.id.in_(ids))
        )
    ).scalars().all()
    conv = await fx.converter(
        session,
        base_ccy=household.base_currency,
        currencies={t.currency for t in rows},
        until=max(t.trade_date for t in rows),
    )
    for txn in rows:
        txn.base_amount, txn.fx_rate_date = await conv.to_base(
            amount=txn.amount,
            currency=txn.currency,
            on=txn.trade_date,
            base_ccy=household.base_currency,
        )


async def _import_rules(session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any],
                        remap: _Remap, result: ImportResult) -> None:
    """Rules key on ``(name, priority)``, and get a fresh id.

    A rule has no id a person chose either, and the pair is what makes two rules
    the same rule to whoever is reading the list. Ordering by that same pair is
    what makes the ordinal stable: an unchanged household exports its rules in one
    order, so "the second rule named Coffee" is the same rule on every import.

    The ids *inside* the blobs are another matter, and are remapped — those are
    real references and the whole reason ``_remap_rule_blob`` exists.
    """
    existing = (
        await session.execute(select(Rule).order_by(Rule.priority, Rule.name, Rule.id))
    ).scalars().all()
    by_name: dict[Any, list[Rule]] = {}
    for rule in existing:
        by_name.setdefault((rule.name.lower(), rule.priority), []).append(rule)
    ordinals = _Ordinals()
    for i, entry in enumerate(_entries(doc, "rules")):
        where = f"rules[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "a rule must carry its id")
        priority = _int(entry, "priority", where) or 100
        name = _text(entry, "name", where) or ""
        key = (name.lower(), priority)
        found = _nth(by_name, key, ordinals.next(key))
        if found is not None:
            remap.put(old_id, found.id)
            result.found("rules")
            continue
        rule = Rule(
            household_id=household_id,
            priority=priority,
            name=name,
            enabled=_bool(entry, "enabled", where, required=False) is not False,
            conditions=_remap_rule_blob(
                _get(entry, "conditions", where), remap, f"{where}.conditions"),
            actions=_remap_rule_blob(_get(entry, "actions", where), remap, f"{where}.actions"),
        )
        session.add(rule)
        await session.flush()
        by_name.setdefault(key, []).append(rule)
        remap.put(old_id, rule.id)
        result.made("rules")


async def _import_fx_rates(session: AsyncSession, doc: dict[str, Any],
                           result: ImportResult) -> None:
    """Insert the rates, leaving any row that already exists exactly as it is.

    ``fx_rates`` is global — no ``household_id``, unique on ``(base, quote,
    rate_date)`` — so a row may already be here from another household or from
    this one's own history. A rate is a fact about the world; two documents that
    disagree about one are a data problem the second import is not qualified to
    resolve, and overwriting would make the ledger's numbers depend on import
    order.
    """
    rows = []
    for i, entry in enumerate(_entries(doc, "fx_rates")):
        where = f"fx_rates[{i}]"
        rows.append({
            "base_currency": (_text(entry, "base_currency", where) or "").upper(),
            "quote_currency": (_text(entry, "quote_currency", where) or "").upper(),
            "rate_date": _date(entry, "rate_date", where),
            "rate": _money(entry, "rate", where),
        })
    if not rows:
        return
    # The count comes back from the database rather than from ``len(rows)``: the
    # second import of a document offers every rate again and the conflict clause
    # quietly takes none of them, so counting what was *offered* would report a
    # fresh import of rates that were already here — the one number this function
    # exists to get right.
    inserted = (
        await session.execute(
            pg_insert(FxRate)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["base_currency", "quote_currency", "rate_date"]
            )
            .returning(FxRate.id)
        )
    ).scalars().all()
    result.made("fx_rates", len(inserted))
    result.found("fx_rates", len(rows) - len(inserted))


# ---- owner income and paystubs (ADR-0052) ----------------------------------


async def _import_income_profiles(session: AsyncSession, household_id: uuid.UUID,
                                   doc: dict[str, Any], remap: _Remap,
                                   result: ImportResult) -> None:
    """One per owner (the model's own unique constraint) — an owner that
    already has a profile on this instance keeps it; the document's copy is
    not a right the target household's own edits since export should lose to."""
    existing_owner_ids = set(
        (await session.execute(select(OwnerIncomeProfile.owner_id))).scalars().all()
    )
    for i, entry in enumerate(_entries(doc, "owner_income_profiles")):
        where = f"owner_income_profiles[{i}]"
        owner_id = remap.get(
            _str_id(_get(entry, "owner_id", where), f"{where}.owner_id"), where, "owner"
        )
        if owner_id in existing_owner_ids:
            result.found("owner_income_profiles")
            continue
        session.add(OwnerIncomeProfile(
            household_id=household_id,
            owner_id=owner_id,
            currency=(_text(entry, "currency", where, required=False) or "USD").upper(),
            annual_gross_income=_money(entry, "annual_gross_income", where, required=False),
            pay_frequency=_text(entry, "pay_frequency", where, required=False),
            filing_status=_text(entry, "filing_status", where, required=False),
            tax_region=_text(entry, "tax_region", where, required=False),
        ))
        existing_owner_ids.add(owner_id)
        result.made("owner_income_profiles")
    await session.flush()


async def _import_paystubs(
    session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any], remap: _Remap,
    result: ImportResult,
) -> tuple[dict[uuid.UUID, uuid.UUID], set[uuid.UUID]]:
    """Always created, never deduped — like transactions, a paystub is a fact
    about one pay date and re-importing the same document is expected to be
    idempotent at the *line* level (below), not by silently dropping rows here.

    Returns ``(old_id -> new_id, {old_ids created})`` for ``_import_paystub_lines``
    to key off, the same shape ``_import_transactions``/``_import_splits`` use.
    """
    rows: dict[uuid.UUID, uuid.UUID] = {}
    created: set[uuid.UUID] = set()
    for i, entry in enumerate(_entries(doc, "paystubs")):
        where = f"paystubs[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "a paystub must carry its id")
        owner_id = remap.get(
            _str_id(_get(entry, "owner_id", where), f"{where}.owner_id"), where, "owner"
        )
        paystub = Paystub(
            household_id=household_id,
            owner_id=owner_id,
            pay_date=_date(entry, "pay_date", where),
            period_start=_date(entry, "period_start", where, required=False),
            period_end=_date(entry, "period_end", where, required=False),
            employer=_text(entry, "employer", where, required=False),
            currency=(_text(entry, "currency", where, required=False) or "USD").upper(),
            gross=_money(entry, "gross", where) or Decimal("0"),
            net=_money(entry, "net", where) or Decimal("0"),
        )
        session.add(paystub)
        await session.flush()
        rows[old_id] = paystub.id
        created.add(old_id)
        result.made("paystubs")
    return rows, created


async def _import_paystub_lines(
    session: AsyncSession, household_id: uuid.UUID, doc: dict[str, Any],
    rows: dict[uuid.UUID, uuid.UUID], created: set[uuid.UUID], result: ImportResult,
) -> None:
    """Lines for the paystubs this import just created — the same "only the
    parent's own new rows" rule ``_import_splits`` follows, and for the same
    reason: an existing paystub is never touched by this import at all."""
    for i, entry in enumerate(_entries(doc, "paystub_lines")):
        where = f"paystub_lines[{i}]"
        old_paystub = _str_id(_get(entry, "paystub_id", where), f"{where}.paystub_id")
        if old_paystub not in created:
            continue
        session.add(PaystubLine(
            household_id=household_id,
            paystub_id=rows[old_paystub],
            kind=_text(entry, "kind", where) or "earning",
            label=_text(entry, "label", where) or "",
            amount=_money(entry, "amount", where) or Decimal("0"),
            ytd_amount=_money(entry, "ytd_amount", where, required=False),
            position=_int(entry, "position", where, required=False) or 0,
        ))
        result.made("paystub_lines")
    await session.flush()


# ---- recurring series (ADR-0053) -------------------------------------------


async def _import_recurring_series(session: AsyncSession, household_id: uuid.UUID,
                                   doc: dict[str, Any], remap: _Remap,
                                   result: ImportResult) -> None:
    """Series key on ``(account_id, lower(name))``, and get a fresh id.

    A series has no id a person chose either, and that pair is what makes two
    series the same series to whoever is reading the list: the same name on the
    same account (``None`` being "any account", which is its own key). The
    ordinal rule ``_import_rules`` uses keeps two genuinely different series that
    happen to share a name apart.

    The account is compared *after* the remap, so a key is "the account this
    document's account became" — matching by the document's old id would find
    nothing on a first import into this household and everything on a second,
    which is exactly backwards.
    """
    existing = (
        await session.execute(
            select(RecurringSeries).order_by(RecurringSeries.name, RecurringSeries.id)
        )
    ).scalars().all()
    by_key: dict[Any, list[RecurringSeries]] = {}
    for series in existing:
        by_key.setdefault((series.account_id, series.name.lower()), []).append(series)
    ordinals = _Ordinals()

    for i, entry in enumerate(_entries(doc, "recurring_series")):
        where = f"recurring_series[{i}]"
        old_id = _str_id(_get(entry, "id", where), f"{where}.id")
        if old_id is None:
            raise _fail(where, "a recurring series must carry its id")
        name = _text(entry, "name", where) or ""
        account_id = remap.get_optional(
            _str_id(_get(entry, "account_id", where), f"{where}.account_id"), "account"
        )
        key = (account_id, name.lower())
        found = _nth(by_key, key, ordinals.next(key))
        if found is not None:
            remap.put(old_id, found.id)
            result.found("recurring_series")
            continue
        series = RecurringSeries(
            household_id=household_id,
            name=name,
            merchant=_text(entry, "merchant", where, required=False),
            account_id=account_id,
            category_id=remap.get_optional(
                _str_id(_get(entry, "category_id", where), f"{where}.category_id"), "category"
            ),
            amount=_money(entry, "amount", where) or Decimal("0"),
            currency=(_text(entry, "currency", where, required=False) or "USD").upper(),
            cadence=_text(entry, "cadence", where) or "monthly",
            next_due_date=_date(entry, "next_due_date", where, required=False),
            is_active=_bool(entry, "is_active", where, required=False) is not False,
            # Optional, not required: the transaction a series was picked from is
            # provenance, and a document that carried the series without that one
            # row should still restore the series. ``get_optional`` records what it
            # dropped so the import's warnings say so.
            transaction_id=remap.get_optional(
                _str_id(_get(entry, "transaction_id", where), f"{where}.transaction_id"),
                "transaction",
            ),
        )
        session.add(series)
        await session.flush()
        by_key.setdefault(key, []).append(series)
        remap.put(old_id, series.id)
        result.made("recurring_series")
    await session.flush()
