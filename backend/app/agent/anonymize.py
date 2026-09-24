"""Server-side anonymization for the agent routes (ADR-0048).

**An allowlist, not a scrubber.** Every field an agent can receive is named in
``policies.REGISTRY`` with the policy that decides what it becomes. A field with no
entry is *dropped* — never passed through — and ``test_agent_policies`` fails until
somebody decides what it is. So adding a column to ``TransactionOut`` cannot leak
it: the new field disappears from agent output and CI goes red, which is the
opposite of the usual failure, where the new field quietly ships.

What survives, and why:

* **Ids, amounts, dates, counts, flags** are kept raw. They are not PII, and the
  bugs an agent is here to find are almost all numeric. (Amounts are still
  sensitive: an agent token is a credential to the household's finances.)
* **Names and free text** — account, owner, security, institution, merchant,
  description, notes — become a **pseudonym**: ``Account 3f9a2c``. It is an HMAC
  of the value under a key derived from the token, so it is *stable* (the same
  merchant is the same pseudonym on every call, so an agent can see two rows
  share one) and *per token* (two tokens' outputs cannot be joined).
* **Text the app writes** — report warnings, check summaries, sync reasons — is
  kept, but every name the household knows is replaced by that name's pseudonym
  first, and number shapes that look like an account number, an SSN or an email
  are masked. The app interpolates account and security names into these
  sentences; substitution is how the sentence survives without them.
* **Text from outside** — a bank's error message — is treated as the app's text
  *plus* masking of anything shaped like a person's name, because it can carry a
  name the household has never told the app.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import typing
import uuid
import zoneinfo
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from app.security.redact import sanitize

# ---------------------------------------------------------------------------
# Pseudonyms and known names
# ---------------------------------------------------------------------------

#: Words that name a *kind* of thing rather than a person or a place, and so are
#: kept where a label is expected. Everything else a label can hold is a
#: pseudonym. Deliberately a short, boring list: adding to it is a decision that a
#: word can never identify anyone.
GENERIC_LABELS: frozenset[str] = frozenset(
    s.casefold()
    for s in (
        # Category and group names in general use (the seed's, and the obvious rest).
        "Salary",
        "Interest",
        "Dividends",
        "Other Income",
        "Income",
        "Groceries",
        "Dining",
        "Housing",
        "Utilities",
        "Transport",
        "Transportation",
        "Shopping",
        "Health",
        "Entertainment",
        "Fees",
        "Transfer",
        "Transfers",
        "Credit Card Payment",
        "Rent",
        "Mortgage",
        "Insurance",
        "Travel",
        "Gifts",
        "Education",
        "Subscriptions",
        "Personal Care",
        "Taxes",
        "Savings",
        "Investments",
        "Expenses",
        "Other",
        "Uncategorized",
        "Bills",
        "Auto",
        "Gas",
        "Fuel",
        "Childcare",
        "Pets",
        "Charity",
        "Coffee",
        "Restaurants",
        "Medical",
        "Phone",
        "Internet",
        "Electricity",
        "Water",
        # The owner every household has (services/owners.SHARED_OWNER_NAME).
        "Shared",
        # Report sentinel labels (services/reports INVESTMENT_LABELS and friends).
        "Investment",
        "Investment income",
        "Investment expense",
        "Investment fees",
        "Unaccounted cash",
        "Cash",
        # Account and security types, as a label for a grouping.
        "depository",
        "credit",
        "investment",
        "loan",
        "other",
        "stock",
        "etf",
        "mutual_fund",
        "bond",
        "option",
        "crypto",
        "cash",
    )
)

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.:\-]{0,63}")
#: Under a key the caller named as a code (``log_level``: ``INFO``), case is free.
_KEYED_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.:\-]{0,63}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^=]{0,11}")
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}|[a-z][a-z0-9\-]{0,31}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+\-]\d{2}:?\d{2})?"
)
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_CATEGORY_KEY = re.compile(
    r"(uncategorized|cash|[a-z_]+:[a-z_]+|cat:[0-9a-f\-]{36}|[0-9a-f\-]{36}|[A-Z]{3}|[a-z_]+)"
)

# Masks for text. Order matters: e-mail before digits (an address can hold them).
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(\.[\w\-]+)+")
_SSN = re.compile(r"(?<![\w.])\d{3}[- ]\d{2}[- ]\d{4}(?![\w])")
#: Six or more digits, optionally split by single spaces or dashes: a card or
#: account number. Not when a decimal part follows (``1234567.0000`` is an amount
#: the app wrote) and not an ISO date (checked in the callback).
_DIGIT_RUN = re.compile(r"(?<![\w.])\d(?:[ \-]?\d){5,}(?!\d|\.\d)")
#: ``x1234``, ``****1234``, ``…4821``, ``ending in 4821`` — the masked forms a bank
#: prints, which are still the tail of an account number.
_MASKED_TAIL = re.compile(
    r"(?i)(?:[x*•.…]{2,}|\bx)\s?\d{2,}|\bending(?:\s+in)?\s+\d{2,}|\b(?:acct|account|a/c)\s*#?\s*\d{2,}"
)
#: For outside text only: a run of two or more Capitalized Words — the shape of a
#: person's or a business's name.
_PROPER_NAME = re.compile(r"\b[A-Z][a-z'’\-]+(?:\s+(?:[A-Z]\.|[A-Z][a-z'’\-]+))+\b")
_LONG_NUMBER = re.compile(r"(?<![\w.])\d{4,}(?!\d|\.\d)")


def _letters(n: int) -> str:
    """``n`` in base 26 over ``a-z``: a placeholder index no mask reads as a number."""
    out = ""
    while True:
        n, r = divmod(n, 26)
        out = chr(97 + r) + out
        if n == 0:
            return out
        n -= 1


def _index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 97) + 1
    return n - 1


def normalize(value: str) -> str:
    """The form two spellings of one name share: whitespace collapsed, case folded."""
    return " ".join(value.split()).casefold()


@dataclass
class KnownNames:
    """Every name the household has told the app, and what kind of thing it names.

    Loaded once per request from the household's own rows (``load_known_names``),
    because the app interpolates these into its own sentences — a report warning
    names the account it is about — and text substitution can only replace a name
    it knows.
    """

    kinds: dict[str, tuple[str, str]] = field(default_factory=dict)  # norm -> (kind, value)

    def add(self, kind: str, value: str | None) -> None:
        if not value or not value.strip():
            return
        norm = normalize(value)
        if norm in GENERIC_LABELS or len(norm) < 2:
            return
        # First kind wins: an account called the same as its owner is still
        # recognisably one pseudonym, whichever it is.
        self.kinds.setdefault(norm, (kind, value))

    def extend(self, kind: str, values: Iterable[str | None]) -> None:
        for v in values:
            self.add(kind, v)

    def lookup(self, value: str) -> tuple[str, str] | None:
        return self.kinds.get(normalize(value))


class Anonymizer:
    """Applies policies with one token's pseudonym key and one household's names."""

    def __init__(self, key: bytes, names: KnownNames | None = None) -> None:
        self._key = key
        self.names = names or KnownNames()
        self._pattern: re.Pattern[str] | None = None
        #: ``Model.field`` for every field met without a policy (and dropped).
        self.unclassified: set[str] = set()

    # -- pseudonyms ----------------------------------------------------------

    def pseudonym(self, kind: str, value: str) -> str:
        digest = hmac.new(
            self._key, f"{kind}\x00{normalize(value)}".encode(), hashlib.sha256
        ).hexdigest()
        return f"{kind} {digest[:6]}"

    def name(self, value: str, *, default_kind: str) -> str:
        """A name: its known kind's pseudonym, so a sentence that mentions an
        account and the account's own ``name`` field agree."""
        known = self.names.lookup(value)
        if known is not None:
            return self.pseudonym(known[0], known[1])
        return self.pseudonym(default_kind, value)

    def label(self, value: str, *, default_kind: str) -> str:
        """A label: kept when it is a generic word, a pseudonym otherwise."""
        if normalize(value) in GENERIC_LABELS:
            return value
        return self.name(value, default_kind=default_kind)

    # -- text ----------------------------------------------------------------

    def _names_pattern(self) -> re.Pattern[str] | None:
        if self._pattern is None and self.names.kinds:
            alternatives = sorted(
                (re.escape(v) for _k, v in self.names.kinds.values()), key=len, reverse=True
            )
            # Whitespace inside a name matches any whitespace, as ``normalize`` does.
            alternatives = [a.replace(r"\ ", r"\s+") for a in alternatives]
            self._pattern = re.compile(
                r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE
            )
        return self._pattern

    def _substitute_names(self, text: str) -> tuple[str, list[str]]:
        """``text`` with each known name replaced by a placeholder, and the
        pseudonyms the placeholders stand for.

        Placeholders rather than the pseudonyms themselves, because the masks run
        next: a pseudonym's six hex digits can be six decimal digits, which is an
        account number's shape, and would be masked. The placeholder is a
        control character the masks do not touch; :meth:`_restore` swaps the
        pseudonyms in once masking is done.
        """
        pattern = self._names_pattern()
        if pattern is None:
            return text, []
        found: list[str] = []

        def _sub(m: re.Match[str]) -> str:
            known = self.names.lookup(m.group(0))
            if known is None:  # pragma: no cover - the pattern is built from the map
                return "[name]"
            found.append(self.pseudonym(known[0], known[1]))
            return f"\x00{_letters(len(found) - 1)}\x00"

        # A control character in the input could forge a placeholder; drop them.
        return pattern.sub(_sub, text.replace("\x00", "")), found

    @staticmethod
    def _restore(text: str, found: list[str]) -> str:
        return re.sub(r"\x00([a-z]+)\x00", lambda m: found[_index(m.group(1))], text)

    @staticmethod
    def _mask_shapes(text: str) -> str:
        text = _EMAIL.sub("[email]", text)
        text = _SSN.sub("[number]", text)
        text = _MASKED_TAIL.sub("[number]", text)

        def _digits(m: re.Match[str]) -> str:
            s = m.group(0)
            if _ISO_DATE.fullmatch(s):
                return s
            return "[number]"

        return _DIGIT_RUN.sub(_digits, text)

    def text(self, value: str) -> str:
        """Text the app wrote: credentials stripped, known names replaced, number
        shapes masked, bounded."""
        text, found = self._substitute_names(sanitize(value))
        return self._restore(self._mask_shapes(text), found)

    def external_text(self, value: str) -> str:
        """Text from outside the app (a bank's error): as :meth:`text`, and then
        anything shaped like a proper name or a long number is masked too."""
        text, found = self._substitute_names(sanitize(value))
        text = self._mask_shapes(text)
        text = _PROPER_NAME.sub("[name]", text)

        def _digits(m: re.Match[str]) -> str:
            s = m.group(0)
            return s if _ISO_DATE.fullmatch(s) else "[number]"

        return self._restore(_LONG_NUMBER.sub(_digits, text), found)

    # -- walking -------------------------------------------------------------

    def walk(self, obj: Any) -> Any:
        """A schema instance (or a list of them), anonymized into JSON data."""
        if obj is None:
            return None
        if isinstance(obj, BaseModel):
            return self.walk_json(type(obj), obj.model_dump(mode="json", by_alias=True))
        if isinstance(obj, (list, tuple)):
            return [self.walk(x) for x in obj]
        raise TypeError(f"cannot anonymize a bare {type(obj).__name__}; wrap it in a schema")

    def walk_json(self, annotation: Any, data: Any) -> Any:
        """JSON ``data`` of type ``annotation``, anonymized field by field.

        Works on the *serialized* response, guided by the schema's types, rather
        than on a re-validated instance: re-validating a route's output runs its
        input validators a second time (a ``null`` regex that was never meant to
        be compiled, say), and what is anonymized is then exactly the bytes the
        page receives — ``"12.3400"``, not whatever a second serialization says.
        """
        if data is None:
            return None
        model = _model_in(annotation)
        if model is not None:
            if isinstance(data, list):
                return [self.walk_json(annotation_item(annotation), x) for x in data]
            if not isinstance(data, dict):
                raise TypeError(f"{model.__name__} expected an object, got {type(data).__name__}")
            out: dict[str, Any] = {}
            for fname, finfo in model.model_fields.items():
                key = finfo.serialization_alias or finfo.alias or fname
                if key not in data:
                    continue
                policy = REGISTRY_REF.get((model, fname))
                if policy is None:
                    self.unclassified.add(f"{model.__name__}.{fname}")
                    continue
                out[key] = policy.apply(data[key], self, finfo.annotation)
            return out
        if isinstance(data, list):
            return [self.walk_json(annotation_item(annotation), x) for x in data]
        raise TypeError(f"cannot anonymize a bare {type(data).__name__}; wrap it in a schema")


def _model_in(annotation: Any) -> type[BaseModel] | None:
    """The schema an annotation names, through ``X | None`` and ``list[X]``."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _model_in(arg)
        if found is not None:
            return found
    return None


def annotation_item(annotation: Any) -> Any:
    """``list[X]`` (or ``list[X] | None``) → ``X``; anything else unchanged."""
    if typing.get_origin(annotation) is list:
        return typing.get_args(annotation)[0]
    for arg in typing.get_args(annotation):
        if typing.get_origin(arg) is list:
            return typing.get_args(arg)[0]
    return annotation


class _RegistryRef:
    """The registry, looked up late: ``policies`` imports this module."""

    def get(self, key):
        from app.agent.policies import REGISTRY

        return REGISTRY.get(key)


REGISTRY_REF = _RegistryRef()


def pseudonym_key(secret: str, token_id: uuid.UUID) -> bytes:
    """The HMAC key for one token's pseudonyms, derived from the app secret."""
    return hmac.new(
        secret.encode(), b"metalmark-agent-pseudonym\x00" + token_id.bytes, hashlib.sha256
    ).digest()


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


class Policy:
    """What one field becomes. ``apply`` takes the field's Python value."""

    #: False for the policies that may keep a string verbatim without looking at
    #: it — the test refuses to put one on a ``str`` field.
    keeps_raw_strings: bool = False

    def apply(
        self, value: Any, anon: Anonymizer, annotation: Any = None
    ) -> Any:  # pragma: no cover
        """``value`` is the field's JSON value; ``annotation`` its declared type."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return type(self).__name__


class Keep(Policy):
    """Kept as is. For ids, amounts, dates, counts and flags — never for a string,
    which the policy test enforces from the field's annotation."""

    keeps_raw_strings = True

    def apply(self, value, anon, annotation=None):
        return value


class Drop(Policy):
    """Removed. The field is present with ``null`` so the shape stays readable."""

    def apply(self, value, anon, annotation=None):
        return None


class Nested(Policy):
    """A schema, or a list of them: walked with their own fields' policies."""

    def apply(self, value, anon, annotation=None):
        return anon.walk_json(annotation, value)


def _each(value, fn):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [None if v is None else fn(v) for v in value]
    return fn(value)


class Pseudonym(Policy):
    """The whole value becomes ``Kind abc123``. For names and free text."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def apply(self, value, anon, annotation=None):
        return _each(value, lambda v: anon.name(str(v), default_kind=self.kind))

    def __repr__(self) -> str:
        return f"Pseudonym({self.kind!r})"


class Label(Pseudonym):
    """A display label: a generic word (``Groceries``) is kept, a name is not."""

    def apply(self, value, anon, annotation=None):
        return _each(value, lambda v: anon.label(str(v), default_kind=self.kind))


class Code(Policy):
    """A value the *app* writes from a fixed vocabulary (a status, an event name).

    Kept if it is one of ``allowed`` — or, with no list, if it has the shape of an
    identifier the app would write (``needs_review``, ``balance.snapshotted``). Any
    other string is not a code, whatever the field says, and becomes a pseudonym.
    """

    def __init__(self, *allowed: str) -> None:
        self.allowed = frozenset(allowed)

    def _ok(self, v: str) -> bool:
        if self.allowed:
            return v in self.allowed
        return bool(_IDENTIFIER.fullmatch(v))

    def apply(self, value, anon, annotation=None):
        return _each(value, lambda v: v if self._ok(str(v)) else anon.pseudonym("Code", str(v)))

    def __repr__(self) -> str:
        return f"Code({', '.join(sorted(self.allowed))})" if self.allowed else "Code(identifier)"


class Pattern(Policy):
    """Kept if the whole value matches ``regex`` (a currency, a ticker, a colour)."""

    def __init__(self, regex: re.Pattern[str], kind: str) -> None:
        self.regex = regex
        self.kind = kind

    def apply(self, value, anon, annotation=None):
        return _each(
            value,
            lambda v: v if self.regex.fullmatch(str(v)) else anon.pseudonym(self.kind, str(v)),
        )

    def __repr__(self) -> str:
        return f"Pattern({self.regex.pattern!r})"


def Currency() -> Pattern:  # noqa: N802 - reads as a policy name at the call site
    return Pattern(_CURRENCY, "Currency")


def Ticker() -> Pattern:  # noqa: N802
    return Pattern(_TICKER, "Ticker")


def Color() -> Pattern:  # noqa: N802
    return Pattern(_COLOR, "Color")


def Key() -> Pattern:  # noqa: N802
    """A grouping key the reports build (``cat:<uuid>``, ``investment:dividend``)."""
    return Pattern(_CATEGORY_KEY, "Key")


class Timezone(Policy):
    def apply(self, value, anon, annotation=None):
        return _each(
            value,
            lambda v: v if v in zoneinfo.available_timezones() else anon.pseudonym("Timezone", v),
        )


class Text(Policy):
    """Text the app composed. See :meth:`Anonymizer.text`."""

    def apply(self, value, anon, annotation=None):
        return _each(value, lambda v: anon.text(str(v)))


class ExternalText(Policy):
    """Text from outside the app. See :meth:`Anonymizer.external_text`."""

    def apply(self, value, anon, annotation=None):
        return _each(value, lambda v: anon.external_text(str(v)))


class JsonTree(Policy):
    """A free-form JSON object (a sync event's ``detail``, ``field_sources``).

    Walked key by key. A key that is not an identifier is dropped with its value.
    Numbers, booleans and nulls are kept; a string is kept only if it is a uuid, a
    date or a number, or — under a key in ``codes`` — an identifier-shaped code.
    Under a key in ``text``/``external`` it is anonymized as that kind of text.
    Every other string becomes a pseudonym (a known name's own, where it is one).
    """

    def __init__(
        self,
        *,
        codes: Iterable[str] = (),
        text: Iterable[str] = (),
        external: Iterable[str] = (),
        values: Iterable[str] = (),
    ) -> None:
        self.codes = frozenset(codes)
        self.text = frozenset(text)
        self.external = frozenset(external)
        #: String values kept under any key (``field_sources``: provider/rule/user).
        self.values = frozenset(values)

    def _string(self, key: str | None, v: str, anon: Anonymizer) -> str:
        if key in self.text:
            return anon.text(v)
        if key in self.external:
            return anon.external_text(v)
        if v in self.values:
            return v
        if key in self.codes and _KEYED_CODE.fullmatch(v):
            return v
        if _UUID.fullmatch(v) or _ISO_DATETIME.fullmatch(v) or _ISO_DATE.fullmatch(v):
            return v
        if _NUMBER.fullmatch(v) and len(v.replace("-", "").split(".")[0]) < 6:
            return v
        return anon.name(v, default_kind="Value")

    def _walk(self, key: str | None, v: Any, anon: Anonymizer) -> Any:
        if v is None or isinstance(v, bool):
            return v
        if isinstance(v, (int, float, Decimal)):
            return to_jsonable_python(v)
        if isinstance(v, (date, datetime, uuid.UUID)):
            return to_jsonable_python(v)
        if isinstance(v, str):
            return self._string(key, v, anon)
        if isinstance(v, dict):
            return {
                k: self._walk(k, item, anon)
                for k, item in v.items()
                if isinstance(k, str) and _IDENTIFIER.fullmatch(k)
            }
        if isinstance(v, (list, tuple)):
            return [self._walk(key, item, anon) for item in v]
        return anon.pseudonym("Value", str(v))

    def apply(self, value, anon, annotation=None):
        return self._walk(None, value, anon)


def annotation_has_str(annotation: Any) -> bool:
    """Whether a field's annotation can carry a string — used by the policy test
    to refuse ``Keep`` on one."""
    if annotation is str:
        return True
    if isinstance(annotation, type) and issubclass(annotation, str):
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return any(isinstance(a, str) for a in typing.get_args(annotation))
    if annotation is dict or origin is dict or annotation is Any:
        return True
    return any(annotation_has_str(a) for a in typing.get_args(annotation))
