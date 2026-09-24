"""starter categories for households that have none

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

A household created before this revision was given no categories, so every row it
synced was uncategorized and nothing could be marked a transfer — a move to an
account the app does not hold has no second leg to link, and only a transfer-typed
category keeps it out of cash flow (found on a live instance, session 06). New
households get the starter set at signup (``services/default_categories.py``);
this gives it to the existing ones.

**Written for a database holding a household's real history.** Only a household
with **no categories at all** is touched: one with even a single category has made
its own choices, and adding forty-odd rows beside them is not a default. Nothing
existing is changed.

The set is a frozen copy of ``DEFAULT_CATEGORIES`` as of this revision — a later
edit to the app's list must not change what this migration did.
``test_migrations_live_data.py`` holds the two equal at this revision.

The ids added are recorded in ``migration_backup`` (see 0006). ``downgrade``
removes only an added category **nothing has used since**: one a transaction, a
split or a rule points at is the household's now, and deleting it would null out
their categorization. A group is removed only once it is empty.
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

BACKUP_SCHEMA = "migration_backup"

DEFAULT_CATEGORIES = [('Income',
  'income',
  [('Paychecks', '💵'),
   ('Interest', '🏦'),
   ('Other Income', '💰'),
   ('Owed to Me', '🤝'),
   ('Tax Refund', '🧾')]),
 ('Transfers',
  'transfer',
  [('Transfer', '🔁'), ('Credit Card Payment', '💳'), ('Loan Repayment', '🏛️')]),
 ('Housing',
  'expense',
  [('Rent', '🏠'),
   ("Insurance – Renter's", '🛡️'),
   ('Home Improvement', '🔨'),
   ('Furniture & Housewares', '🛋️'),
   ('Kitchen', '🍳')]),
 ('Bills & Utilities', 'expense', [('Bills – Communication', '📱'), ('Utilities', '💡')]),
 ('Food & Dining',
  'expense',
  [('Groceries', '🛒'), ('Restaurants', '🍽️'), ('Bars & Coffee Shops', '☕')]),
 ('Auto & Transport',
  'expense',
  [('Gas', '⛽'),
   ('Auto Maintenance', '🔧'),
   ('Parking & Tolls', '🅿️'),
   ('Insurance – Auto', '🚗'),
   ('Car Payments', '🚘'),
   ('Car Purchase', '🏎️'),
   ('Car Rental', '🚙'),
   ('Car – Other', '🛞'),
   ('Public Transit', '🚆'),
   ('Taxi & Ride Shares', '🚕'),
   ('Bicycle', '🚲')]),
 ('Travel', 'expense', [('Travel & Vacation', '✈️')]),
 ('Shopping',
  'expense',
  [('Clothing', '👕'),
   ('Electronics', '💻'),
   ('Books', '📚'),
   ('Stationery', '✏️'),
   ('Jewelry', '💍'),
   ('Arts & Crafts', '🎨'),
   ('Cosmetics', '💄'),
   ('Shipping', '📦')]),
 ('Subscriptions',
  'expense',
  [('Subscription – News', '📰'),
   ('Subscription – Privacy', '🔒'),
   ('Subscription – Productivity', '🗂️'),
   ('Subscription – Dev / SaaS', '🧑\u200d💻'),
   ('Subscription – Entertainment', '📺'),
   ('Domain Registration', '🌐'),
   ('Storage', '🗄️'),
   ('Personal Infrastructure', '🖥️')]),
 ('Entertainment',
  'expense',
  [('Movie Theater', '🎬'),
   ('Events', '🎟️'),
   ('Creator Support', '🎙️'),
   ('Physical & Digital Media', '💿'),
   ('Video Games', '🎮')]),
 ('Health & Wellness',
  'expense',
  [('Fitness', '🏋️'),
   ('Medical', '🩺'),
   ('Dentist', '🦷'),
   ('Medical Equipment', '🩹'),
   ('Haircut', '💈')]),
 ('Education', 'expense', [('Education – Study Material', '📖'), ('Education – Courses', '🎓')]),
 ('Gifts & Giving', 'expense', [('Gifts', '🎁'), ('Charity', '💝')]),
 ('Family & People',
  'expense',
  [('Family', '👪'), ('Family – Admin', '📋'), ('Other People', '🧑\u200d🤝\u200d🧑')]),
 ('Financial',
  'expense',
  [('Taxes', '🏛'),
   ('Financial & Legal Services', '⚖️'),
   ('Financial Fees', '💸'),
   ('Immigration', '🛂'),
   ('Checks', '📝'),
   ('Cash & ATM', '🏧')])]


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {BACKUP_SCHEMA}"))
    conn.execute(text(f"REVOKE ALL ON SCHEMA {BACKUP_SCHEMA} FROM PUBLIC"))
    conn.execute(
        text(
            f"""
            CREATE TABLE {BACKUP_SCHEMA}.r0009_added (
                kind text NOT NULL,
                id uuid PRIMARY KEY
            )
            """
        )
    )
    households = conn.execute(
        text(
            """
            SELECT h.id FROM households h
            WHERE NOT EXISTS (SELECT 1 FROM categories c WHERE c.household_id = h.id)
            """
        )
    ).scalars().all()
    for hid in households:
        for g_sort, (group_name, group_type, categories) in enumerate(DEFAULT_CATEGORIES):
            gid = conn.execute(
                text(
                    "INSERT INTO category_groups (household_id, name, type, sort) "
                    "VALUES (:hid, :name, :type, :sort) RETURNING id"
                ),
                {"hid": hid, "name": group_name, "type": group_type, "sort": g_sort},
            ).scalar_one()
            conn.execute(
                text(f"INSERT INTO {BACKUP_SCHEMA}.r0009_added (kind, id) VALUES ('group', :id)"),
                {"id": gid},
            )
            for c_sort, (name, icon) in enumerate(categories):
                cid = conn.execute(
                    text(
                        "INSERT INTO categories (household_id, group_id, name, icon, rollover, "
                        "sort) VALUES (:hid, :gid, :name, :icon, false, :sort) RETURNING id"
                    ),
                    {"hid": hid, "gid": gid, "name": name, "icon": icon, "sort": c_sort},
                ).scalar_one()
                conn.execute(
                    text(
                        f"INSERT INTO {BACKUP_SCHEMA}.r0009_added (kind, id) "
                        "VALUES ('category', :id)"
                    ),
                    {"id": cid},
                )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            f"""
            DELETE FROM categories c
            USING {BACKUP_SCHEMA}.r0009_added a
            WHERE a.kind = 'category' AND c.id = a.id
              AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.category_id = c.id)
              AND NOT EXISTS (SELECT 1 FROM transaction_splits s WHERE s.category_id = c.id)
              AND NOT EXISTS (
                  SELECT 1 FROM rules r
                  WHERE r.actions::text LIKE '%' || c.id::text || '%'
                     OR r.conditions::text LIKE '%' || c.id::text || '%'
              )
            """
        )
    )
    conn.execute(
        text(
            f"""
            DELETE FROM category_groups g
            USING {BACKUP_SCHEMA}.r0009_added a
            WHERE a.kind = 'group' AND g.id = a.id
              AND NOT EXISTS (SELECT 1 FROM categories c WHERE c.group_id = g.id)
            """
        )
    )
    conn.execute(text(f"DROP TABLE {BACKUP_SCHEMA}.r0009_added"))
