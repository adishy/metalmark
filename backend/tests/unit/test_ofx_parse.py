"""OFX/QFX parsing (ADR-0030 §1-3): the dialect sniff, the refusal, and the parse.

These are unit tests: no database, no session. What they pin is the parser's own
promises — that a 1.x SGML file is refused *by name* rather than guessed at, that
XML is parsed with the parser this module exists to use and not the standard
library's, that nothing downstream of ``parse_ofx`` ever sees a string that is
secretly money, and that a row which cannot be read honestly is reported rather
than coerced.

The fixtures are hand-authored and say so (``tests/fixtures/ofx/README.md``): the
wire format is public, and a real statement is a household's ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import ofx
from app.services.errors import LedgerError

D = Decimal

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ofx"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---- Detection -------------------------------------------------------------


def test_the_dialect_is_decided_by_the_leading_bytes():
    """Detection is by header, never by "parse it and see": a 1.x file is
    well-formed enough to fool a lenient reader for a while, and by the time it is
    not, a value has already been mis-split."""
    assert ofx.detect_format(_fixture("legacy.ofx")) == ofx.OFX_1X
    assert ofx.detect_format(_fixture("statement.ofx")) == ofx.OFX_2X
    assert ofx.detect_format(_fixture("statement.qfx")) == ofx.OFX_2X  # <?OFX …?>
    assert ofx.detect_format(b"Date,Amount\n2026-01-05,-5.00\n") == ofx.NOT_OFX
    assert ofx.detect_format(b"") == ofx.NOT_OFX


def test_a_bom_and_leading_blank_lines_do_not_hide_the_header():
    """Both are what a file that has been through a text editor looks like, and
    a BOM would otherwise put three invisible bytes in front of ``OFXHEADER:``."""
    assert ofx.detect_format(b"\xef\xbb\xbfOFXHEADER:100\n") == ofx.OFX_1X
    assert ofx.detect_format(b"\n\n  \xef\xbb\xbf<?xml version='1.0'?><OFX/>") == ofx.OFX_2X
    assert ofx.detect_format(b"   \n\t<?OFX OFXHEADER='200'?>") == ofx.OFX_2X


def test_a_1x_file_is_refused_by_name_and_told_the_way_out():
    """The message *is* the mitigation (ADR-0030 §1): it has to name the format
    found and what to do instead, because the alternative — guessing where
    unquoted SGML values end — corrupts an amount with nothing to show for it."""
    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(_fixture("legacy.ofx"))

    assert exc.value.status == 400
    message = exc.value.message
    assert "OFX 1.x" in message
    assert "SGML" in message
    # The way out, named concretely: another export, or the CSV path.
    assert "OFX 2.x" in message and "QFX" in message and "CSV" in message
    # And no part of the file itself is echoed back into the response.
    assert "000111222333" not in message


def test_a_file_that_is_not_ofx_at_all_is_refused():
    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(b"Date,Description,Amount\n2026-01-05,Coffee,-5.00\n")
    assert exc.value.status == 400
    assert "not an OFX or QFX file" in exc.value.message


def test_malformed_xml_is_refused_as_malformed():
    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(_fixture("malformed.ofx"))
    assert exc.value.status == 400
    assert "not well-formed XML" in exc.value.message


def test_xml_with_no_statement_in_it_is_refused():
    raw = b"<?xml version='1.0'?><OFX><SIGNONMSGSRSV1><SONRS/></SIGNONMSGSRSV1></OFX>"
    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(raw)
    assert exc.value.status == 400
    assert "no OFX bank statement" in exc.value.message


def test_an_oversized_file_is_refused_before_the_parser_sees_it():
    raw = b"<?xml version='1.0'?><OFX>" + b" " * (ofx.MAX_FILE_BYTES + 1)
    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(raw)
    assert exc.value.status == 400
    assert "MB" in exc.value.message


# ---- Entities: the reason this module uses defusedxml ----------------------


def test_a_nested_entity_bomb_is_refused():
    """Well-formed XML that a non-defused parser would happily expand."""
    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(_fixture("entities.ofx"))
    assert exc.value.status == 400
    assert "unsafe" in exc.value.message
    # The exception's own text is not echoed: for an entity bomb it contains the
    # entity's *value*, which is a thing the attacker chose.
    assert "lol" not in exc.value.message


def test_defusedxml_is_the_load_bearing_part_not_the_stdlib():
    """A single long entity, which is the case the standard library does *not*
    catch.

    CPython's expat refuses a *nested* bomb on its own (an input-amplification
    limit added in 3.12 — ``entities.ofx`` is refused by both parsers there), so
    the nested fixture alone would not prove this dependency does anything. One
    entity expanded many times over is the case where ``xml.etree`` hands back a
    document thousands of times larger than the file and ``defusedxml`` refuses
    it, which is exactly the property ADR-0030 §2 buys.
    """
    import xml.etree.ElementTree as stdlib

    bomb = (
        b"<?xml version='1.0'?>\n"
        b"<!DOCTYPE OFX [ <!ENTITY a '" + b"A" * 2000 + b"'> ]>\n"
        b"<OFX><STMTRS><BANKACCTFROM><ACCTID>&a;&a;&a;&a;</ACCTID>"
        b"</BANKACCTFROM></STMTRS></OFX>"
    )
    # The standard library parses it and expands it, four times over.
    expanded = stdlib.fromstring(bomb).findtext(".//ACCTID")
    assert expanded is not None and len(expanded) == 8000

    with pytest.raises(LedgerError) as exc:
        ofx.parse_ofx(bomb)
    assert exc.value.status == 400
    assert "unsafe" in exc.value.message


# ---- The parse -------------------------------------------------------------


def test_a_statement_parses_into_normalized_rows():
    parsed = ofx.parse_ofx(_fixture("statement.ofx"))
    statement = parsed.primary

    assert parsed.org == "Harborline Credit Union"
    assert (statement.acct_id, statement.acct_type, statement.currency) == (
        "000111222333", "CHECKING", "USD",
    )
    assert statement.ledger_balance == D("4210.55")
    assert statement.errors == ()
    assert [t.fitid for t in parsed.transactions] == [
        "2026010500001", "2026010700001", "2026011200001", "2026012100001",
    ]
    assert [t.amount for t in parsed.transactions] == [
        D("-42.75"), D("2400.00"), D("-18.40"), D("-96.10"),
    ]
    # Every amount is a Decimal, never a float (ADR-0005) — a float here would be
    # a cent lost somewhere later, silently.
    assert all(isinstance(t.amount, Decimal) for t in parsed.transactions)
    assert all(t.transacted_at.tzinfo is not None for t in parsed.transactions)


def test_an_ofx_timestamp_carries_its_own_zone():
    """``[-5:EST]`` is an offset, not decoration: these are stored as instants and
    bucketed into months, so reading 23:30 Eastern as UTC moves the row's day —
    and at a month boundary, the report the household is looking at."""
    parsed = ofx.parse_ofx(_fixture("statement.ofx"))
    first = parsed.transactions[0]
    assert first.posted_at == datetime(2026, 1, 5, 17, 0, tzinfo=UTC)
    assert first.transacted_at == first.posted_at


def test_a_date_with_no_clock_becomes_noon_utc():
    """Midnight would fall on the previous day for a household west of UTC and
    move the row's month at the boundary — the CSV path makes the same choice."""
    raw = (
        b"<?xml version='1.0'?><OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
        b"<BANKACCTFROM><ACCTID>1</ACCTID></BANKACCTFROM><BANKTRANLIST>"
        b"<STMTTRN><DTPOSTED>20260105</DTPOSTED><TRNAMT>-5.00</TRNAMT>"
        b"<FITID>x1</FITID></STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS>"
        b"</BANKMSGSRSV1></OFX>"
    )
    assert ofx.parse_ofx(raw).transactions[0].transacted_at == datetime(
        2026, 1, 5, 12, tzinfo=UTC
    )


def test_the_description_is_the_memo_and_the_payee_is_left_for_the_rules():
    """``<NAME>`` is not written to ``merchant``: ``create_transaction`` marks a
    supplied merchant ``user``, and a human's field outranks every rule — which
    would silently stop a rule that renames merchants from touching an import
    (ADR-0030 §7)."""
    parsed = ofx.parse_ofx(_fixture("statement.ofx"))
    assert parsed.transactions[0].description == "POS PURCHASE 0117"
    # The row with no <MEMO> falls back to its <NAME>.
    assert parsed.transactions[1].description == "DIRECT DEPOSIT"


def test_a_row_with_an_unreadable_amount_or_date_is_an_error_not_a_guess():
    raw = (
        b"<?xml version='1.0'?><OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
        b"<BANKACCTFROM><ACCTID>1</ACCTID></BANKACCTFROM><BANKTRANLIST>"
        b"<STMTTRN><DTPOSTED>20260105</DTPOSTED><TRNAMT>not a number</TRNAMT></STMTTRN>"
        b"<STMTTRN><DTPOSTED>20260106666666</DTPOSTED><TRNAMT>-5.00</TRNAMT></STMTTRN>"
        b"<STMTTRN><DTPOSTED>20260107</DTPOSTED><TRNAMT>-6.00</TRNAMT>"
        b"<FITID>ok</FITID></STMTTRN>"
        b"</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    )
    parsed = ofx.parse_ofx(raw)
    # The two bad rows are reported where they sat, and the good one still lands:
    # OFX has no line numbers, so "the second transaction in the file" is what a
    # human can go and find.
    assert [e.position for e in parsed.errors] == [1, 2]
    assert "unreadable amount" in parsed.errors[0].message
    assert "unreadable date" in parsed.errors[1].message
    assert [t.fitid for t in parsed.transactions] == ["ok"]


def test_an_amount_that_is_not_plain_decimal_notation_is_refused():
    """``Decimal`` accepts ``NaN`` and ``Infinity``, and neither survives being
    stored in NUMERIC(19,4) or compared in the ledger."""
    for text in ("NaN", "Infinity", "-Infinity", "1e5", "1,234.00", "$5.00"):
        raw = (
            b"<?xml version='1.0'?><OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
            b"<BANKACCTFROM><ACCTID>1</ACCTID></BANKACCTFROM><BANKTRANLIST>"
            b"<STMTTRN><DTPOSTED>20260105</DTPOSTED><TRNAMT>" + text.encode() + b"</TRNAMT>"
            b"</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
        )
        parsed = ofx.parse_ofx(raw)
        assert parsed.transactions == (), text
        assert len(parsed.errors) == 1, text


def test_investment_rows_are_counted_and_not_parsed():
    """<BUYSTOCK> is not a cash outflow (ADR-0030 §5): importing it as one
    double-counts the movement the moment investments exist as objects. The count
    is what makes an all-investment file read as "0 imported, 5 skipped" rather
    than as a silent success."""
    parsed = ofx.parse_ofx(_fixture("investments.ofx"))
    assert parsed.transactions == ()
    assert parsed.investment_count == 5
    # <DTSTART>/<DTEND> are the list's own bounds, not transactions.
    assert parsed.primary.acct_id == "BRK-000777888"


def test_a_file_may_hold_more_than_one_statement():
    """A bank that exports two accounts at once: the human chooses the account to
    import into and the file's <ACCTID> is reported rather than obeyed
    (ADR-0030 §4), so merging their rows costs nothing the preview does not say."""
    raw = (
        b"<?xml version='1.0'?><OFX>"
        b"<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>A</ACCTID>"
        b"</BANKACCTFROM><BANKTRANLIST><STMTTRN><DTPOSTED>20260105</DTPOSTED>"
        b"<TRNAMT>-5.00</TRNAMT><FITID>a1</FITID></STMTTRN></BANKTRANLIST>"
        b"<LEDGERBAL><BALAMT>10.00</BALAMT><DTASOF>20260131</DTASOF></LEDGERBAL>"
        b"</STMTRS></STMTTRNRS></BANKMSGSRSV1>"
        b"<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>B</ACCTID>"
        b"</BANKACCTFROM><BANKTRANLIST><STMTTRN><DTPOSTED>20260106</DTPOSTED>"
        b"<TRNAMT>-6.00</TRNAMT><FITID>b1</FITID></STMTTRN></BANKTRANLIST>"
        b"</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    )
    parsed = ofx.parse_ofx(raw)
    assert len(parsed.statements) == 2
    assert parsed.primary.acct_id == "A"  # the preview describes the first
    assert [t.fitid for t in parsed.transactions] == ["a1", "b1"]


def test_tags_are_matched_without_a_namespace_getting_in_the_way():
    raw = (
        b"<?xml version='1.0'?><OFX xmlns='http://example.com/ofx'>"
        b"<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM><ACCTID>1</ACCTID>"
        b"</BANKACCTFROM><BANKTRANLIST><STMTTRN><DTPOSTED>20260105</DTPOSTED>"
        b"<TRNAMT>-5.00</TRNAMT><FITID>n1</FITID></STMTTRN></BANKTRANLIST>"
        b"</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    )
    parsed = ofx.parse_ofx(raw)
    assert [t.fitid for t in parsed.transactions] == ["n1"]
    assert parsed.primary.acct_id == "1"


def test_a_deeply_nested_document_does_not_blow_the_stack():
    """``<a><a><a>…`` nests as deep as the file is long, and a recursive walk over
    a hostile 2 MB file is a RecursionError arriving as a 500 instead of a
    refusal."""
    depth = 4000
    raw = (
        b"<?xml version='1.0'?><OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS>"
        b"<BANKACCTFROM><ACCTID>1</ACCTID></BANKACCTFROM><BANKTRANLIST>"
        b"<STMTTRN><DTPOSTED>20260105</DTPOSTED><TRNAMT>-5.00</TRNAMT>"
        + b"<X>" * depth
        + b"<FITID>deep</FITID>"
        + b"</X>" * depth
        + b"</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    )
    assert [t.fitid for t in ofx.parse_ofx(raw).transactions] == ["deep"]


def test_a_qfx_file_parses_without_an_xml_declaration():
    """QFX is OFX 2.x from Quicken, and the shape that trips a naive sniffer: no
    XML declaration, a ``<?OFX …?>`` processing instruction instead."""
    parsed = ofx.parse_ofx(_fixture("statement.qfx"))
    assert parsed.org == "Harborline Credit Union"
    assert [t.amount for t in parsed.transactions] == [D("-7.25")]
    assert parsed.primary.ledger_balance == D("4203.30")
