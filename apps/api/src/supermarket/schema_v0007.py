"""Append-only compensating documents; originals and source costs remain unchanged."""

import sqlalchemy as sa
from sqlalchemy.sql.schema import SchemaItem

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
    "sales_invoice": ["id", "business_id", "store_id"],
    "sales_item": ["id", "business_id", "store_id", "product_id"],
    "sales_stock_allocation": ["id", "business_id"],
    "supplier_payment": ["id", "business_id"],
    "purchase": ["id", "business_id", "store_id"],
    "purchase_item": ["id", "business_id"],
    "stock_batch": ["id", "business_id", "store_id", "product_id"],
    "stock_movement": ["id", "business_id", "store_id", "product_id"],
    "financial_movement": ["id", "business_id", "store_id", "account_id"],
}.items():
    sa.Table(name, metadata, *(uid(c, primary_key=c == "id") for c in columns))


def fk(target: str, column: str, *scope: str) -> sa.ForeignKeyConstraint:
    names = ["business_id", *scope]
    return sa.ForeignKeyConstraint(
        [*names, column], [*(target + "." + c for c in names), target + ".id"]
    )


def header(name: str, target: str, *extra: SchemaItem) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        uid(primary_key=True),
        uid("business_id"),
        uid("store_id"),
        uid("target_id"),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        *actor_columns(),
        timestamp(),
        *extra,
        fk(
            target,
            "target_id",
            *(
                ["store_id", "product_id"]
                if target == "stock_batch"
                else ["store_id"]
                if target in {"purchase", "sales_invoice"}
                else []
            ),
        ),
        sa.UniqueConstraint("business_id", "store_id", "id"),
        *command_constraints(),
    )


credits = header(
    "credit_note",
    "sales_invoice",
    sa.Column("kind", sa.String(12), nullable=False),
    sa.Column("taxable_total", sa.Numeric(14, 2), nullable=False),
    sa.Column("cgst", sa.Numeric(14, 2), nullable=False),
    sa.Column("sgst", sa.Numeric(14, 2), nullable=False),
    sa.CheckConstraint(
        "kind IN ('return', 'cancel') AND total >= 0 AND taxable_total >= 0 "
        "AND cgst >= 0 AND sgst >= 0 AND total = taxable_total + cgst + "
        "sgst"
    ),
    sa.Index("ix_credit_sale", "business_id", "store_id", "target_id"),
)
credit_items = sa.Table(
    "credit_item",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("credit_id"),
    uid("sale_item_id"),
    uid("product_id"),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("disposition", sa.String(12), nullable=False),
    *(
        sa.Column(c, sa.Numeric(14, 2), nullable=False)
        for c in ["total", "taxable", "cgst", "sgst"]
    ),
    fk("credit_note", "credit_id", "store_id"),
    fk("sales_item", "sale_item_id", "store_id", "product_id"),
    sa.UniqueConstraint("business_id", "store_id", "product_id", "id"),
    sa.UniqueConstraint("business_id", "credit_id", "sale_item_id"),
    sa.CheckConstraint(
        "quantity > 0 AND disposition IN ('restock', 'discard') AND total "
        ">= 0 AND taxable >= 0 AND cgst >= 0 AND sgst >= 0 AND total = "
        "taxable + cgst + sgst"
    ),
    sa.Index("ix_credit_item_original", "business_id", "sale_item_id"),
)
credit_stock = sa.Table(
    "credit_stock_allocation",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("product_id"),
    uid("credit_item_id"),
    uid("original_allocation_id"),
    uid("movement_id"),
    sa.Column("damage_movement_id", sa.Uuid(as_uuid=False)),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    fk("credit_item", "credit_item_id", "store_id", "product_id"),
    fk("sales_stock_allocation", "original_allocation_id"),
    fk("stock_movement", "movement_id", "store_id", "product_id"),
    fk("stock_movement", "damage_movement_id", "store_id", "product_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint("quantity > 0"),
)
refunds = sa.Table(
    "credit_refund",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("credit_id"),
    uid("account_id"),
    uid("movement_id"),
    sa.Column("amount", sa.Numeric(14, 2), nullable=False),
    fk("credit_note", "credit_id", "store_id"),
    fk("financial_movement", "movement_id", "store_id", "account_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint("amount > 0"),
)
payment_reversals = header(
    "supplier_payment_reversal",
    "supplier_payment",
    uid("account_id"),
    uid("movement_id"),
    fk("financial_movement", "movement_id", "store_id", "account_id"),
    sa.UniqueConstraint("business_id", "target_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint("total > 0"),
)
purchase_reversals = header(
    "purchase_reversal",
    "purchase",
    *(
        sa.Column(c, sa.Numeric(14, 2), nullable=False)
        for c in ["taxable_total", "cgst", "sgst", "igst", "round_off"]
    ),
    sa.UniqueConstraint("business_id", "target_id"),
    sa.CheckConstraint("total > 0 AND total = taxable_total + cgst + sgst + igst + round_off"),
)
purchase_reversal_items = sa.Table(
    "purchase_reversal_item",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("product_id"),
    uid("reversal_id"),
    uid("purchase_item_id"),
    uid("movement_id"),
    fk("purchase_reversal", "reversal_id", "store_id"),
    fk("purchase_item", "purchase_item_id"),
    fk("stock_movement", "movement_id", "store_id", "product_id"),
    sa.UniqueConstraint("business_id", "purchase_item_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
)
adjustments = header(
    "stock_adjustment",
    "stock_batch",
    uid("product_id"),
    uid("movement_id"),
    sa.Column("expected_count", sa.Numeric(15, 3), nullable=False),
    sa.Column("actual_count", sa.Numeric(15, 3), nullable=False),
    fk("stock_movement", "movement_id", "store_id", "product_id"),
    sa.CheckConstraint(
        "expected_count >= 0 AND actual_count >= 0 AND expected_count <> actual_count AND total = 0"
    ),
    sa.UniqueConstraint("business_id", "movement_id"),
)
TABLE_NAMES = (
    "credit_note",
    "credit_item",
    "credit_stock_allocation",
    "credit_refund",
    "supplier_payment_reversal",
    "purchase_reversal",
    "purchase_reversal_item",
    "stock_adjustment",
)
