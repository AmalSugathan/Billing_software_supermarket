"""Append-only accounts, expenses, money movements and cashier reconciliation."""

import sqlalchemy as sa

from supermarket.catalog_models import timestamp, uid

metadata = sa.MetaData()
sa.Table("user_account", metadata, uid(primary_key=True))
sa.Table("store", metadata, uid(primary_key=True), uid("business_id"))
sa.Table("terminal", metadata, uid(primary_key=True), uid("business_id"), uid("store_id"))


def actor_columns() -> list[sa.Column[str] | sa.Column[bool]]:
    return [
        sa.Column(
            "actor_user_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("user_account.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("human_approved", sa.Boolean, nullable=False),
        sa.Column("idempotency_key", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
    ]


def command_constraints() -> list[sa.Constraint]:
    return [
        sa.UniqueConstraint("business_id", "idempotency_key"),
        sa.CheckConstraint("human_approved AND source = 'human'"),
    ]


accounts = sa.Table(
    "financial_account",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    sa.Column("terminal_id", sa.Uuid(as_uuid=False)),
    sa.Column("name", sa.String(150), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("opening_amount", sa.Numeric(14, 2), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "store_id", "id"),
    sa.UniqueConstraint("business_id", "store_id", "name"),
    sa.UniqueConstraint("business_id", "terminal_id"),
    sa.ForeignKeyConstraint(["business_id", "store_id"], ["store.business_id", "store.id"]),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "terminal_id"],
        ["terminal.business_id", "terminal.store_id", "terminal.id"],
    ),
    sa.CheckConstraint("kind IN ('cash', 'bank', 'upi', 'card') AND opening_amount >= 0"),
    sa.CheckConstraint(
        "(kind = 'cash' AND terminal_id IS NOT NULL) OR (kind <> 'cash' AND terminal_id IS NULL)"
    ),
    *command_constraints(),
)
sessions = sa.Table(
    "cash_session",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("account_id"),
    sa.Column("opening_cash", sa.Numeric(14, 2), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "store_id", "account_id", "id"),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id"],
        ["financial_account.business_id", "financial_account.store_id", "financial_account.id"],
    ),
    sa.CheckConstraint("opening_cash >= 0"),
    *command_constraints(),
    sa.Index("ix_cash_session_account", "business_id", "store_id", "account_id"),
)
movements = sa.Table(
    "financial_movement",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("account_id"),
    sa.Column("cash_session_id", sa.Uuid(as_uuid=False)),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    sa.Column("resource_id", sa.Uuid(as_uuid=False), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "store_id", "account_id", "id"),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id"],
        ["financial_account.business_id", "financial_account.store_id", "financial_account.id"],
    ),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id", "cash_session_id"],
        [
            "cash_session.business_id",
            "cash_session.store_id",
            "cash_session.account_id",
            "cash_session.id",
        ],
    ),
    sa.CheckConstraint(
        "(kind = 'opening' AND amount >= 0 AND cash_session_id IS NULL) OR "
        "(kind = 'opening_variance' AND amount <> 0 AND cash_session_id IS NULL) "
        "OR (kind IN ('expense', 'withdrawal') AND amount < 0) OR (kind "
        "IN ('receipt', 'expense_reversal') AND amount > 0) OR (kind = "
        "'cash_variance' AND amount <> 0 AND cash_session_id IS NOT NULL)"
    ),
    *command_constraints(),
    sa.Index(
        "ix_financial_movement_account_date", "business_id", "store_id", "account_id", "created_at"
    ),
    sa.Index("ix_financial_movement_session", "business_id", "cash_session_id"),
)
expenses = sa.Table(
    "expense",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("account_id"),
    uid("movement_id"),
    sa.Column("reference", sa.String(100), nullable=False),
    sa.Column("reference_identity", sa.String(100), nullable=False),
    sa.Column("expense_date", sa.Date, nullable=False),
    sa.Column("category", sa.String(80), nullable=False),
    sa.Column("description", sa.String(500), nullable=False),
    sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "store_id", "account_id", "id"),
    sa.UniqueConstraint("business_id", "store_id", "reference_identity"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id", "movement_id"],
        [
            "financial_movement.business_id",
            "financial_movement.store_id",
            "financial_movement.account_id",
            "financial_movement.id",
        ],
    ),
    sa.CheckConstraint("amount > 0"),
    *command_constraints(),
    sa.Index("ix_expense_store_date", "business_id", "store_id", "expense_date"),
)
reversals = sa.Table(
    "expense_reversal",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("account_id"),
    uid("expense_id"),
    uid("movement_id"),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "expense_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id", "expense_id"],
        ["expense.business_id", "expense.store_id", "expense.account_id", "expense.id"],
    ),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id", "movement_id"],
        [
            "financial_movement.business_id",
            "financial_movement.store_id",
            "financial_movement.account_id",
            "financial_movement.id",
        ],
    ),
    *command_constraints(),
)
closings = sa.Table(
    "cash_closing",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("account_id"),
    uid("cash_session_id"),
    sa.Column("movement_id", sa.Uuid(as_uuid=False)),
    sa.Column("expected_cash", sa.Numeric(14, 2), nullable=False),
    sa.Column("actual_cash", sa.Numeric(14, 2), nullable=False),
    sa.Column("variance", sa.Numeric(14, 2), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "cash_session_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id", "cash_session_id"],
        [
            "cash_session.business_id",
            "cash_session.store_id",
            "cash_session.account_id",
            "cash_session.id",
        ],
    ),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "account_id", "movement_id"],
        [
            "financial_movement.business_id",
            "financial_movement.store_id",
            "financial_movement.account_id",
            "financial_movement.id",
        ],
    ),
    sa.CheckConstraint(
        "expected_cash >= 0 AND actual_cash >= 0 AND variance = actual_cash - expected_cash"
    ),
    sa.CheckConstraint(
        "(variance = 0 AND movement_id IS NULL) OR (variance <> 0 AND movement_id IS NOT NULL)"
    ),
    *command_constraints(),
)
TABLE_NAMES = (
    "financial_account",
    "cash_session",
    "financial_movement",
    "expense",
    "expense_reversal",
    "cash_closing",
)
