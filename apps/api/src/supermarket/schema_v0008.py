"""Bounded offline authorizations, stock reservations and immutable sync journals."""

import sqlalchemy as sa

from supermarket.catalog_models import timestamp, uid


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


metadata = sa.MetaData()
for name, columns in {
    "user_account": ["id"],
    "terminal": ["id", "business_id", "store_id"],
    "cash_session": ["id", "business_id", "store_id", "account_id"],
    "stock_batch": ["id", "business_id", "store_id", "product_id"],
    "stock_movement": ["id", "business_id", "store_id", "product_id"],
    "sales_invoice": ["id", "business_id", "store_id"],
}.items():
    sa.Table(name, metadata, *(uid(c, primary_key=c == "id") for c in columns))

leases = sa.Table(
    "offline_lease",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("terminal_id"),
    uid("account_id"),
    uid("cash_session_id"),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.UniqueConstraint("business_id", "store_id", "id"),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "terminal_id"],
        ["terminal.business_id", "terminal.store_id", "terminal.id"],
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
    *command_constraints(),
)
reservations = sa.Table(
    "offline_reservation",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("lease_id"),
    uid("product_id"),
    uid("batch_id"),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("unit_cost", sa.Numeric(18, 6), nullable=False),
    sa.Column("product_snapshot", sa.JSON, nullable=False),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "lease_id"],
        ["offline_lease.business_id", "offline_lease.store_id", "offline_lease.id"],
    ),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "product_id", "batch_id"],
        [
            "stock_batch.business_id",
            "stock_batch.store_id",
            "stock_batch.product_id",
            "stock_batch.id",
        ],
    ),
    sa.UniqueConstraint("business_id", "store_id", "product_id", "id"),
    sa.CheckConstraint("quantity > 0 AND unit_cost >= 0"),
    sa.Index("ix_offline_reserved_batch", "business_id", "batch_id"),
)
journal = sa.Table(
    "offline_sale",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("lease_id"),
    uid("sale_id"),
    sa.Column("sequence", sa.Integer, nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    timestamp(),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "lease_id"],
        ["offline_lease.business_id", "offline_lease.store_id", "offline_lease.id"],
    ),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "sale_id"],
        ["sales_invoice.business_id", "sales_invoice.store_id", "sales_invoice.id"],
    ),
    sa.UniqueConstraint("business_id", "lease_id", "sequence"),
    sa.UniqueConstraint("business_id", "sale_id"),
    sa.CheckConstraint("sequence > 0"),
)
consumptions = sa.Table(
    "offline_consumption",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("product_id"),
    uid("reservation_id"),
    uid("movement_id"),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "product_id", "reservation_id"],
        [
            "offline_reservation.business_id",
            "offline_reservation.store_id",
            "offline_reservation.product_id",
            "offline_reservation.id",
        ],
    ),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "product_id", "movement_id"],
        [
            "stock_movement.business_id",
            "stock_movement.store_id",
            "stock_movement.product_id",
            "stock_movement.id",
        ],
        deferrable=True,
        initially="DEFERRED",
    ),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint("quantity > 0"),
)
seals = sa.Table(
    "offline_seal",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("target_id"),
    sa.Column("last_sequence", sa.Integer, nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    *actor_columns(),
    timestamp(),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "target_id"],
        ["offline_lease.business_id", "offline_lease.store_id", "offline_lease.id"],
    ),
    sa.UniqueConstraint("business_id", "target_id"),
    sa.CheckConstraint("last_sequence >= 0"),
    *command_constraints(),
)
TABLE_NAMES = (
    "offline_lease",
    "offline_reservation",
    "offline_sale",
    "offline_consumption",
    "offline_seal",
)
