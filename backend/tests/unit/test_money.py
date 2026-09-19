"""Property + unit tests for the money primitives (ADR-0005, ADR-0015)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.money import allocate, convert, quantize_money, to_money

pytestmark = pytest.mark.unit

money_amounts = st.decimals(
    min_value=Decimal("-1000000"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)
positive_weights = st.lists(
    st.integers(min_value=1, max_value=1000).map(Decimal), min_size=1, max_size=8
)


def test_to_money_rejects_float():
    with pytest.raises(TypeError):
        to_money(1.23)


@given(total=money_amounts, weights=positive_weights)
def test_allocate_sums_exactly_to_total(total, weights):
    parts = allocate(total, weights, currency="USD")
    assert sum(parts, Decimal(0)) == quantize_money(total, "USD")


@given(total=money_amounts, weights=positive_weights)
def test_allocate_no_part_is_wildly_off(total, weights):
    parts = allocate(total, weights, currency="USD")
    # Every part is quantized to the cent.
    for p in parts:
        assert p == p.quantize(Decimal("0.01"))


def test_allocate_remainder_to_largest():
    # 10.00 split 1:1:1 -> 3.34 / 3.33 / 3.33 (remainder to the largest/first).
    parts = allocate(Decimal("10.00"), [Decimal(1), Decimal(1), Decimal(1)], "USD")
    assert sum(parts, Decimal(0)) == Decimal("10.00")
    assert max(parts) == Decimal("3.34")


def test_jpy_has_no_minor_unit():
    parts = allocate(Decimal("100"), [Decimal(1), Decimal(1), Decimal(1)], "JPY")
    assert sum(parts, Decimal(0)) == Decimal("100")
    for p in parts:
        assert p == p.quantize(Decimal("1"))


def test_convert_quantizes_to_storage_scale():
    # 1 JPY -> USD at 0.00636 with high-precision rate.
    base = convert(Decimal("1000"), Decimal("0.00636000"))
    assert base == Decimal("6.3600")
