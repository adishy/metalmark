"""Money & rounding primitives (ADR-0005).

All monetary values are :class:`decimal.Decimal` end to end; floats are never
used for money. Amounts are stored as ``NUMERIC(19,4)`` (4dp). FX rates are
``NUMERIC(19,8)`` (8dp) — 4dp is wrong for low-value currencies (1 JPY ≈
0.00636 USD), see ARCHITECTURE §2.

Rounding policy (single, documented, tested):
  * Monetary rounding is **half-up** to the currency's minor unit.
  * When splitting a parent amount into N children, we round each child and
    assign the leftover remainder to the **largest** child so the children
    always sum **exactly** to the parent (no drift).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, getcontext

# Wide precision so intermediate products (amount × rate) never lose digits
# before we quantize.
getcontext().prec = 38

CENTS = Decimal("0.0001")  # NUMERIC(19,4) minor unit used internally
RATE_QUANT = Decimal("0.00000001")  # NUMERIC(19,8)

# Currencies whose minor unit is not 1/100. Extend as needed.
_MINOR_UNITS: dict[str, Decimal] = {
    "JPY": Decimal("1"),
    "KRW": Decimal("1"),
    "USD": Decimal("0.01"),
    "EUR": Decimal("0.01"),
    "GBP": Decimal("0.01"),
}
_DEFAULT_MINOR = Decimal("0.01")


def minor_unit(currency: str) -> Decimal:
    """The smallest representable unit for a currency (e.g. 0.01 USD, 1 JPY)."""
    return _MINOR_UNITS.get(currency.upper(), _DEFAULT_MINOR)


def to_money(value: str | int | Decimal) -> Decimal:
    """Coerce an input to a Decimal, rejecting floats to prevent silent drift."""
    if isinstance(value, float):
        raise TypeError("Refusing to build money from float; pass str/Decimal/int.")
    return Decimal(value)


def quantize_money(value: Decimal, currency: str = "USD") -> Decimal:
    """Round to a currency's minor unit, half-up."""
    return value.quantize(minor_unit(currency), rounding=ROUND_HALF_UP)


def quantize_storage(value: Decimal) -> Decimal:
    """Round to the storage scale NUMERIC(19,4). Used for base_amount caches."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def allocate(total: Decimal, weights: Sequence[Decimal], currency: str = "USD") -> list[Decimal]:
    """Split ``total`` across ``weights`` so the parts sum EXACTLY to ``total``.

    Each part is rounded to the currency minor unit; the rounding remainder is
    assigned to the largest part (ties → earliest). Used for percentage splits
    and for allocating a parent's base_amount across split children.
    """
    if not weights:
        raise ValueError("allocate() needs at least one weight")
    wsum = sum(weights, Decimal(0))
    if wsum <= 0:
        raise ValueError("weights must sum to a positive value")

    unit = minor_unit(currency)
    raw = [total * w / wsum for w in weights]
    rounded = [r.quantize(unit, rounding=ROUND_HALF_UP) for r in raw]
    remainder = total - sum(rounded, Decimal(0))

    if remainder != 0:
        # Assign the whole remainder to the largest part so the sum is exact.
        idx = max(range(len(rounded)), key=lambda i: (rounded[i], -i))
        rounded[idx] = rounded[idx] + remainder
    return rounded


def convert(amount: Decimal, rate: Decimal) -> Decimal:
    """Convert a native amount to base using a rate, quantized to storage scale.

    ``base = amount * rate``. Rate direction is documented at the call site
    (FX service); this helper only does the arithmetic + rounding.
    """
    return quantize_storage(amount * rate)
