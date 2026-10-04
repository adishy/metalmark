"""Account-local holding corrections, additive and safe on populated databases.

Revision ID: 0016
Revises: 0015
"""
from sqlalchemy import inspect, text

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

COLUMNS = {
    "is_override": "BOOLEAN NOT NULL DEFAULT false",
    "name_override": "VARCHAR(200)",
    "ticker_override": "VARCHAR(32)",
    "security_type_override": "VARCHAR(16)",
    # The total, the quantity it was set at and the date it applies from: one fact
    # in three columns, all null or all set.
    "market_value_override": "NUMERIC(19,4)",
    "market_value_override_quantity": "NUMERIC(19,8)",
    "market_value_override_as_of": "DATE",
}


CONSTRAINTS = {
    "ck_holdings_override_value_complete": (
        "(market_value_override IS NULL AND market_value_override_quantity IS NULL "
        "AND market_value_override_as_of IS NULL) OR "
        "(market_value_override IS NOT NULL AND market_value_override_quantity IS NOT NULL "
        "AND market_value_override_as_of IS NOT NULL AND market_value_override_quantity <> 0)"
    ),
    "ck_holdings_override_type_valid": (
        "security_type_override IS NULL OR security_type_override IN "
        "('stock','etf','mutual_fund','bond','option','crypto','cash','other')"
    ),
}


def upgrade() -> None:
    conn = op.get_bind()
    existing = {c["name"] for c in inspect(conn).get_columns("holdings")}
    for name, definition in COLUMNS.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE holdings ADD COLUMN {name} {definition}"))
    constraints = {c["name"] for c in inspect(conn).get_check_constraints("holdings")}
    for name, expression in CONSTRAINTS.items():
        if name not in constraints:
            conn.execute(text(f"ALTER TABLE holdings ADD CONSTRAINT {name} CHECK ({expression})"))


def downgrade() -> None:
    # Shape-only downgrade: quantities, basis, dates and source are unchanged.
    conn = op.get_bind()
    existing = {c["name"] for c in inspect(conn).get_columns("holdings")}
    for name in CONSTRAINTS:
        conn.execute(text(f"ALTER TABLE holdings DROP CONSTRAINT IF EXISTS {name}"))
    for name in reversed(COLUMNS):
        if name in existing:
            conn.execute(text(f"ALTER TABLE holdings DROP COLUMN {name}"))
