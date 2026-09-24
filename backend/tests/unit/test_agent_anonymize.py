"""The anonymizer's policies and the registry's completeness (ADR-0048).

Two kinds of test, and the second is the one that matters over time:

* **Policy behaviour** — each policy on hand-picked inputs: what survives, what
  becomes a pseudonym, what is masked.
* **Registry completeness** — derived from the app, not listed by hand: every
  field of every schema an agent can receive has a policy, no string field is
  kept raw, and a canary planted in *every* string field of *every* schema never
  comes out the other side. Adding a schema or a field is what makes these run
  on it.
"""

from __future__ import annotations

import json
import os
import typing
import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

os.environ.setdefault("METALMARK_SECRET_KEY", "unit-test-secret")

from app.agent import dispatch  # noqa: E402
from app.agent.anonymize import (  # noqa: E402
    GENERIC_LABELS,
    Anonymizer,
    Code,
    ExternalText,
    JsonTree,
    Keep,
    KnownNames,
    Label,
    Pseudonym,
    Text,
    annotation_has_str,
    pseudonym_key,
)
from app.agent.policies import REGISTRY  # noqa: E402
from app.schemas import agent as agent_schemas  # noqa: E402

pytestmark = pytest.mark.unit

KEY = pseudonym_key("secret", uuid.UUID(int=1))
OTHER_KEY = pseudonym_key("secret", uuid.UUID(int=2))

#: A person's identifying details, in the shapes they turn up in bank data.
NAME = "Zelda Quixote"
SSN = "123-45-6789"
CARD = "4111 1111 1111 1111"
ACCOUNT_NO = "000123456789"
EMAIL = "zelda.q@example.com"
CANARIES = [NAME, SSN, CARD, ACCOUNT_NO, EMAIL, "4111111111111111", "Quixote", "zelda"]


def _names(*pairs: tuple[str, str]) -> KnownNames:
    names = KnownNames()
    for kind, value in pairs:
        names.add(kind, value)
    return names


def _anon(*pairs: tuple[str, str], key: bytes = KEY) -> Anonymizer:
    return Anonymizer(key, _names(*pairs))


def _leaks(text: str) -> list[str]:
    lowered = text.lower()
    return [c for c in CANARIES if c.lower() in lowered]


# ---- pseudonyms --------------------------------------------------------------


def test_a_pseudonym_is_the_kind_and_six_hex_digits():
    p = _anon().pseudonym("Merchant", "Starbucks")
    assert p.startswith("Merchant ")
    assert len(p.split(" ")[1]) == 6
    int(p.split(" ")[1], 16)


def test_a_pseudonym_is_stable_and_ignores_case_and_spacing():
    a = _anon()
    assert a.pseudonym("Merchant", "Starbucks") == a.pseudonym("Merchant", "  STARBUCKS ")
    assert a.pseudonym("Merchant", "Blue  Bottle") == a.pseudonym("Merchant", "blue bottle")


def test_different_values_and_different_kinds_get_different_pseudonyms():
    a = _anon()
    assert a.pseudonym("Merchant", "Starbucks") != a.pseudonym("Merchant", "Peets")
    assert a.pseudonym("Merchant", "Starbucks") != a.pseudonym("Account", "Starbucks")


def test_two_tokens_cannot_join_their_pseudonyms():
    assert _anon().pseudonym("Account", "Checking") != _anon(key=OTHER_KEY).pseudonym(
        "Account", "Checking"
    )


def test_the_key_depends_on_the_secret_and_the_token():
    t = uuid.uuid4()
    assert pseudonym_key("a", t) == pseudonym_key("a", t)
    assert pseudonym_key("a", t) != pseudonym_key("b", t)
    assert pseudonym_key("a", t) != pseudonym_key("a", uuid.uuid4())


def test_a_known_name_keeps_its_own_kind_wherever_it_appears():
    a = _anon(("Account", "Zelda's Checking"))
    # The same account, reached through a field that defaults to another kind.
    assert Pseudonym("Merchant").apply("Zelda's Checking", a) == a.pseudonym(
        "Account", "Zelda's Checking"
    )


# ---- labels and codes ----------------------------------------------------------


@pytest.mark.parametrize("word", ["Groceries", "groceries", "Shared", "Uncategorized", "Rent"])
def test_a_generic_label_is_kept(word):
    assert Label("Category").apply(word, _anon()) == word


def test_a_personal_label_is_a_pseudonym():
    a = _anon()
    assert Label("Category").apply("Zelda's allowance", a) == a.pseudonym(
        "Category", "Zelda's allowance"
    )


def test_a_generic_word_is_never_learned_as_a_name():
    # Otherwise "Groceries" in a warning would be replaced by a pseudonym.
    names = _names(("Category", "Groceries"), ("Owner", "Shared"))
    assert names.kinds == {}


def test_a_code_outside_its_vocabulary_is_a_pseudonym():
    policy = Code("reviewed", "needs_review")
    a = _anon()
    assert policy.apply("reviewed", a) == "reviewed"
    assert policy.apply(NAME, a).startswith("Code ")


def test_an_open_code_keeps_only_identifier_shapes():
    policy = Code()
    a = _anon()
    assert policy.apply("balance.snapshotted", a) == "balance.snapshotted"
    assert policy.apply(NAME, a).startswith("Code ")
    assert policy.apply("Zelda", a).startswith("Code ")


def test_a_list_is_anonymized_element_by_element():
    a = _anon()
    assert Code("a", "b").apply(["a", "zz"], a)[0] == "a"
    assert Code("a", "b").apply(["a", "zz"], a)[1].startswith("Code ")


def test_none_stays_none():
    for policy in (Pseudonym("X"), Label("X"), Code(), Text(), ExternalText()):
        assert policy.apply(None, _anon()) is None


# ---- text --------------------------------------------------------------------


def test_text_replaces_known_names_with_their_pseudonyms():
    a = _anon(("Account", "Zelda Checking"), ("Security", "Quixote Fund"))
    out = a.text(
        "no FX rate for EUR: Zelda Checking's currency move is not attributable; "
        "quixote   fund has no price"
    )
    assert "Zelda" not in out and "uixote" not in out
    assert a.pseudonym("Account", "Zelda Checking") in out
    assert a.pseudonym("Security", "Quixote Fund") in out
    assert "no FX rate for EUR" in out


def test_text_does_not_replace_inside_a_longer_word():
    a = _anon(("Owner", "Al"))
    assert a.text("Also, Al paid.") == f"Also, {a.pseudonym('Owner', 'Al')} paid."


def test_text_prefers_the_longest_known_name():
    a = _anon(("Owner", "Zelda"), ("Account", "Zelda Savings"))
    assert a.text("Zelda Savings") == a.pseudonym("Account", "Zelda Savings")


@pytest.mark.parametrize(
    "raw",
    [
        f"SSN {SSN}",
        f"card {CARD}",
        f"acct {ACCOUNT_NO}",
        "card 4111-1111-1111-1111",
        f"mail {EMAIL} now",
        "ACCT #4821 overdrawn",
        "card ending in 4821",
        "xx4821 payment",
        "****4821",
    ],
)
def test_text_masks_number_and_address_shapes(raw):
    out = _anon().text(raw)
    assert "[number]" in out or "[email]" in out
    for token in ("4821", "6789", "1111", "zelda.q"):
        assert token not in out


@pytest.mark.parametrize(
    "raw",
    [
        "Of the unexplained change, 1234567.0000 USD is not attributable to any account",
        "no rate for a EUR transaction on 2026-09-21: it is not attributable",
        "3 synced investment account(s) valued from their balance",
        "HTTP 403 from the bridge",
    ],
)
def test_text_keeps_amounts_dates_and_counts(raw):
    assert _anon().text(raw) == raw


def test_text_strips_credentials_first():
    out = _anon().text("GET https://user:pass@bridge.example/accounts failed")
    assert "user:pass" not in out


def test_external_text_masks_a_name_the_app_never_heard_of():
    out = _anon().external_text("Account for Zelda Quixote is locked (ref 88213)")
    assert "Zelda" not in out and "Quixote" not in out and "88213" not in out
    assert "[name]" in out


def test_external_text_keeps_a_status_code_and_the_known_pseudonyms():
    a = _anon(("Institution", "Quixote Credit Union"))
    out = a.external_text("Quixote Credit Union answered HTTP 403")
    assert out == f"{a.pseudonym('Institution', 'Quixote Credit Union')} answered HTTP 403"


# ---- JSON trees ----------------------------------------------------------------


def test_a_tree_keeps_structure_numbers_ids_and_dates():
    tid = str(uuid.uuid4())
    tree = {
        "account_id": tid,
        "count": 3,
        "ok": True,
        "on": "2026-09-01",
        "none": None,
        "at": "2026-09-01T10:00:00+00:00",
        "ratio": 1.5,
    }
    assert JsonTree().apply(tree, _anon()) == tree


def test_a_tree_pseudonymizes_every_other_string():
    a = _anon(("Account", NAME))
    out = JsonTree().apply(
        {
            "name": NAME,
            "names": [NAME, "Other Person"],
            "key": ACCOUNT_NO + ":x",
            "nested": {"deep": [{"x": EMAIL}]},
        },
        a,
    )
    assert _leaks(json.dumps(out)) == []
    assert out["name"] == a.pseudonym("Account", NAME)


def test_a_tree_drops_keys_that_are_not_identifiers():
    out = JsonTree().apply({NAME: 1, "fine_key": 2}, _anon())
    assert out == {"fine_key": 2}


def test_a_tree_anonymizes_text_keys_as_text_and_keeps_code_keys():
    a = _anon(("Account", "Zelda Checking"))
    policy = JsonTree(codes=("kind",), text=("reason",), external=("error",))
    out = policy.apply(
        {
            "kind": "auth",
            "reason": "Zelda Checking was not reported",
            "error": "Zelda Quixote locked out",
        },
        a,
    )
    assert out["kind"] == "auth"
    assert out["reason"] == f"{a.pseudonym('Account', 'Zelda Checking')} was not reported"
    assert "Quixote" not in out["error"]


def test_a_long_number_string_in_a_tree_is_not_kept_as_a_number():
    out = JsonTree().apply({"n": "12", "acct": ACCOUNT_NO}, _anon())
    assert out["n"] == "12"
    assert out["acct"] != ACCOUNT_NO


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(max_size=40),
        lambda children: (
            st.lists(children, max_size=4)
            | st.dictionaries(st.text(max_size=12), children, max_size=4)
        ),
        max_leaves=20,
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_any_tree_is_json_and_keeps_no_long_digit_run(tree):
    """Fuzz: whatever a detail holds, the output serializes, and no string of
    six or more digits (an account number) comes out as a string value."""
    out = JsonTree().apply({"root": tree}, _anon())
    dumped = json.dumps(out)

    def strings(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, list):
            for x in v:
                yield from strings(x)
        elif isinstance(v, dict):
            for x in v.values():
                yield from strings(x)

    for s in strings(out):
        assert not (s.isdigit() and len(s) >= 6), dumped


# ---- the registry, derived from the app -----------------------------------------


def _models_in(tp, out: dict) -> None:
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        if tp in out:
            return
        out[tp] = None
        for f in tp.model_fields.values():
            _models_in(f.annotation, out)
        return
    for arg in typing.get_args(tp):
        _models_in(arg, out)


def agent_visible_models() -> list[type[BaseModel]]:
    """Every schema an agent can receive: each exposed route's response model and
    the debug views' own, and everything nested in them."""
    found: dict = {}
    for e in dispatch.exposed():
        _models_in(e.response_model, found)
    for model in (
        agent_schemas.TransactionExplainOut,
        agent_schemas.BalanceExplainOut,
        agent_schemas.SystemOut,
    ):
        _models_in(model, found)
    return list(found)


def test_the_exposed_routes_are_the_apps_get_routes_minus_the_excluded():
    app_paths = {r.path for r in dispatch._app_get_routes()}
    exposed = {e.path for e in dispatch.exposed()}
    assert set(dispatch.EXCLUDED) <= app_paths, "an EXCLUDED entry names no route"
    assert exposed == app_paths - set(dispatch.EXCLUDED)


def test_every_agent_visible_field_has_a_policy():
    missing = [
        f"{m.__module__}.{m.__name__}.{name}"
        for m in agent_visible_models()
        for name in m.model_fields
        if (m, name) not in REGISTRY
    ]
    assert missing == [], (
        "These fields reach agents with no anonymization policy (they are dropped "
        "until they get one). Add them to app/agent/policies.py:\n" + "\n".join(missing)
    )


def test_no_string_field_is_kept_raw():
    offenders = [
        f"{m.__name__}.{name}: {f.annotation}"
        for m in agent_visible_models()
        for name, f in m.model_fields.items()
        if isinstance(REGISTRY.get((m, name)), Keep) and annotation_has_str(f.annotation)
    ]
    assert offenders == [], "Keep on a field that can hold a string:\n" + "\n".join(offenders)


def test_every_string_query_parameter_is_patterned_or_refused():
    open_params = []
    for e in dispatch.exposed():
        for p in e.route.dependant.query_params:
            if dispatch._is_free_string(p.field_info.annotation) and (
                p.name not in dispatch.STRING_PARAMS and p.name not in dispatch.REFUSED_PARAMS
            ):
                open_params.append(f"{e.path}?{p.name}")
    assert open_params == []


def test_search_is_refused():
    assert "search" in dispatch.REFUSED_PARAMS


# ---- the canary: every string field of every schema ------------------------------

CANARY_TEXT = f"{NAME} {SSN} {CARD} {EMAIL} acct {ACCOUNT_NO}"


def _sample(annotation, depth=0):
    """A value for a field, with the canary in every string position."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or (origin is not None and type(None) in args and origin is not list):
        non_none = [a for a in args if a is not type(None)]
        return _sample(non_none[0], depth)
    if origin is typing.Literal:
        return args[0]
    if origin is list:
        return [_sample(args[0], depth), _sample(args[0], depth)]
    if origin is dict or annotation is dict:
        return {
            "name": CANARY_TEXT,
            "reason": CANARY_TEXT,
            "error": CANARY_TEXT,
            "nested": {"x": [CANARY_TEXT]},
            NAME: CANARY_TEXT,
        }
    if annotation is typing.Any:
        return {"any": CANARY_TEXT}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _instance(annotation, depth + 1)
    if annotation in (str,) or (isinstance(annotation, type) and issubclass(annotation, str)):
        return CANARY_TEXT
    return {
        uuid.UUID: str(uuid.uuid4()),
        int: 7,
        bool: True,
        Decimal: "12.3400",
        date: "2026-09-01",
        datetime: "2026-09-01T00:00:00Z",
        float: 1.5,
    }.get(annotation, CANARY_TEXT)


def _instance(model: type[BaseModel], depth: int = 0) -> dict:
    """The JSON a route would send for ``model``, with the canary in every string —
    including the fields no real value would put one in, like an e-mail."""
    return {name: _sample(f.annotation, depth) for name, f in model.model_fields.items()}


@pytest.mark.parametrize("model", agent_visible_models(), ids=lambda m: m.__name__)
def test_a_canary_in_every_string_field_never_comes_out(model):
    # The name is known to the household, as a real account or owner name is.
    anon = _anon(("Owner", NAME))
    out = json.dumps(anon.walk_json(model, _instance(model)))
    assert _leaks(out) == [], out
    assert anon.unclassified == set()


def test_an_unregistered_field_is_dropped_and_reported():
    class Stray(BaseModel):
        secret: str

    anon = _anon()
    assert anon.walk(Stray(secret=NAME)) == {}
    assert anon.unclassified == {"Stray.secret"}


def test_the_generic_list_holds_no_personal_looking_entries():
    # Every entry is one or three words of the app's vocabulary; a new entry that
    # looks like a name ("Mary") should be argued for in review, not slipped in.
    for word in GENERIC_LABELS:
        assert len(word.split()) <= 3, word


def test_placeholder_indices_round_trip():
    from app.agent.anonymize import _index, _letters

    for n in (0, 1, 25, 26, 27, 701, 702, 5000):
        assert _index(_letters(n)) == n
    assert len({_letters(n) for n in range(2000)}) == 2000


def test_text_with_many_known_names_restores_each_one():
    pairs = [("Account", f"Zelda Account {chr(65 + i % 26)}{i}") for i in range(1200)]
    a = _anon(*pairs)
    target = pairs[1100][1]
    out = a.text(f"{target} and {pairs[3][1]}")
    assert out == f"{a.pseudonym('Account', target)} and {a.pseudonym('Account', pairs[3][1])}"


def test_a_control_character_cannot_forge_a_placeholder():
    a = _anon(("Owner", NAME))
    assert "\x00" not in a.text("\x00a\x00 " + NAME)


def test_a_list_response_is_walked_item_by_item():
    from app.schemas.owners import OwnerOut

    a = _anon()
    out = a.walk_json(
        list[OwnerOut],
        [
            {"id": str(uuid.uuid4()), "name": NAME, "kind": "person", "sort": 1},
            {"id": str(uuid.uuid4()), "name": "Shared", "kind": "shared", "sort": 0},
        ],
    )
    assert out[0]["name"] == a.pseudonym("Owner", NAME)
    assert out[1]["name"] == "Shared"


def test_a_value_keeps_the_apps_own_serialization():
    from app.schemas.ledger import NetWorthOut

    out = _anon().walk_json(
        NetWorthOut,
        {
            "base_currency": "USD",
            "assets": "12.3400",
            "liabilities": "-1.0000",
            "net_worth": "11.3400",
            "unconverted_currencies": [],
            "attribution": "account",
        },
    )
    assert out["assets"] == "12.3400"
