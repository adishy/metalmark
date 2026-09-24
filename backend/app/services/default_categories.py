"""The starter categories every new household gets.

Each group carries the type (``income`` | ``expense`` | ``transfer``) that every
report reads — the category's own type *is* its group's (ARCHITECTURE §2) — and
each category an emoji in ``categories.icon``. The **Transfer** group is the one
that matters most to the numbers: a move to an account the app does not hold has
no second leg to link, so a transfer-typed category is the only thing that keeps
it out of cash flow.

"Uncategorized" is deliberately not here. It is the absence of a category
(``category_id IS NULL``) everywhere the reports look, and a row named that would
be a second, disagreeing spelling of the same state.

Household-specific categories (a relative's name, say) are not defaults: this
list ships in a public repository and image, and a household adds its own.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, CategoryGroup

# (group name, group type, [(category name, emoji), ...]) — in display order.
DEFAULT_CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("Income", "income", [
        ("Paychecks", "💵"),
        ("Interest", "🏦"),
        ("Other Income", "💰"),
        ("Owed to Me", "🤝"),
        ("Tax Refund", "🧾"),
    ]),
    ("Transfers", "transfer", [
        ("Transfer", "🔁"),
        ("Credit Card Payment", "💳"),
        ("Loan Repayment", "🏛️"),
    ]),
    ("Housing", "expense", [
        ("Rent", "🏠"),
        ("Insurance – Renter's", "🛡️"),
        ("Home Improvement", "🔨"),
        ("Furniture & Housewares", "🛋️"),
        ("Kitchen", "🍳"),
    ]),
    ("Bills & Utilities", "expense", [
        ("Bills – Communication", "📱"),
        ("Utilities", "💡"),
    ]),
    ("Food & Dining", "expense", [
        ("Groceries", "🛒"),
        ("Restaurants", "🍽️"),
        ("Bars & Coffee Shops", "☕"),
    ]),
    ("Auto & Transport", "expense", [
        ("Gas", "⛽"),
        ("Auto Maintenance", "🔧"),
        ("Parking & Tolls", "🅿️"),
        ("Insurance – Auto", "🚗"),
        ("Car Payments", "🚘"),
        ("Car Purchase", "🏎️"),
        ("Car Rental", "🚙"),
        ("Car – Other", "🛞"),
        ("Public Transit", "🚆"),
        ("Taxi & Ride Shares", "🚕"),
        ("Bicycle", "🚲"),
    ]),
    ("Travel", "expense", [
        ("Travel & Vacation", "✈️"),
    ]),
    ("Shopping", "expense", [
        ("Clothing", "👕"),
        ("Electronics", "💻"),
        ("Books", "📚"),
        ("Stationery", "✏️"),
        ("Jewelry", "💍"),
        ("Arts & Crafts", "🎨"),
        ("Cosmetics", "💄"),
        ("Shipping", "📦"),
    ]),
    ("Subscriptions", "expense", [
        ("Subscription – News", "📰"),
        ("Subscription – Privacy", "🔒"),
        ("Subscription – Productivity", "🗂️"),
        ("Subscription – Dev / SaaS", "🧑‍💻"),
        ("Subscription – Entertainment", "📺"),
        ("Domain Registration", "🌐"),
        ("Storage", "🗄️"),
        ("Personal Infrastructure", "🖥️"),
    ]),
    ("Entertainment", "expense", [
        ("Movie Theater", "🎬"),
        ("Events", "🎟️"),
        ("Creator Support", "🎙️"),
        ("Physical & Digital Media", "💿"),
        ("Video Games", "🎮"),
    ]),
    ("Health & Wellness", "expense", [
        ("Fitness", "🏋️"),
        ("Medical", "🩺"),
        ("Dentist", "🦷"),
        ("Medical Equipment", "🩹"),
        ("Haircut", "💈"),
    ]),
    ("Education", "expense", [
        ("Education – Study Material", "📖"),
        ("Education – Courses", "🎓"),
    ]),
    ("Gifts & Giving", "expense", [
        ("Gifts", "🎁"),
        ("Charity", "💝"),
    ]),
    ("Family & People", "expense", [
        ("Family", "👪"),
        ("Family – Admin", "📋"),
        ("Other People", "🧑‍🤝‍🧑"),
    ]),
    ("Financial", "expense", [
        ("Taxes", "🏛"),
        ("Financial & Legal Services", "⚖️"),
        ("Financial Fees", "💸"),
        ("Immigration", "🛂"),
        ("Checks", "📝"),
        ("Cash & ATM", "🏧"),
    ]),
]


async def install_defaults(session: AsyncSession, household_id: uuid.UUID) -> int:
    """Add the starter set to a household that has **no categories**. Returns how many.

    A household with even one category has made its own choices, and adding
    forty-odd rows beside them is not a default any more — so this is a no-op
    there, which is also what makes it safe to call twice.
    """
    existing = (
        await session.execute(
            select(func.count(Category.id)).where(Category.household_id == household_id)
        )
    ).scalar_one()
    if existing:
        return 0
    added = 0
    for g_sort, (group_name, group_type, categories) in enumerate(DEFAULT_CATEGORIES):
        group = CategoryGroup(
            household_id=household_id, name=group_name, type=group_type, sort=g_sort
        )
        session.add(group)
        await session.flush()
        for c_sort, (name, icon) in enumerate(categories):
            session.add(
                Category(
                    household_id=household_id, group_id=group.id, name=name, icon=icon,
                    sort=c_sort,
                )
            )
            added += 1
    await session.flush()
    return added
