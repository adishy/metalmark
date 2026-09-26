"""SQLAlchemy models. Import all here so Alembic sees the full metadata."""

from app.models.agent import AgentToken  # noqa: F401
from app.models.fx import FxRate  # noqa: F401
from app.models.identity import (  # noqa: F401
    Household,
    HouseholdMember,
    Session,
    User,
)
from app.models.income import OwnerIncomeProfile, Paystub, PaystubLine  # noqa: F401
from app.models.institution import InstitutionLogo  # noqa: F401
from app.models.investments import (  # noqa: F401
    Holding,
    InvestmentTransaction,
    Security,
    SecurityPrice,
)
from app.models.ledger import (  # noqa: F401
    Account,
    AccountConnection,
    BalanceSnapshot,
    Category,
    CategoryGroup,
    Owner,
    Rule,
    Tag,
    Transaction,
    TransactionSplit,
    TransactionTag,
    TransferGroup,
)
from app.models.sync import SyncJob, SyncRun, SyncRunEvent  # noqa: F401

__all__ = [
    "AgentToken",
    "FxRate",
    "Household",
    "HouseholdMember",
    "Session",
    "User",
    "Holding",
    "InstitutionLogo",
    "InvestmentTransaction",
    "Security",
    "SecurityPrice",
    "Account",
    "AccountConnection",
    "BalanceSnapshot",
    "Category",
    "CategoryGroup",
    "Owner",
    "OwnerIncomeProfile",
    "Paystub",
    "PaystubLine",
    "Rule",
    "Tag",
    "SyncJob",
    "SyncRun",
    "SyncRunEvent",
    "Transaction",
    "TransactionSplit",
    "TransactionTag",
    "TransferGroup",
]
