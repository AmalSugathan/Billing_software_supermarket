"""Normalized immutable POS receipts and invoice-linked supplier payments."""

from decimal import Decimal

import sqlalchemy as sa

from supermarket.catalog_models import timestamp, uid
from supermarket.finance_models import actor_columns, command_constraints

metadata = sa.MetaData()
for name, columns in {
    "user_account": ["id"],
    "store": ["id", "business_id"],
    "terminal": ["id", "business_id", "store_id"],
    "product": ["id", "business_id"],
    "stock_batch": ["id", "business_id", "store_id", "product_id"],
    "stock_movement": ["id", "business_id", "store_id", "product_id"],
    "financial_account": ["id", "business_id", "store_id"],
    "financial_movement": ["id", "business_id", "store_id", "account_id"],
    "purchase": ["id", "business_id", "store_id"],
}.items():
    sa.Table(name, metadata, *(uid(column, primary_key=column == "id") for column in columns))


def scoped(target: str, *columns: str) -> sa.ForeignKeyConstraint:
    names = ["business_id", "store_id", *columns]
    return sa.ForeignKeyConstraint(names, [target + "." + c for c in names[:-1]] + [target + ".id"])


def money(name: str) -> sa.Column[Decimal]:
    return sa.Column(name, sa.Numeric(14, 2), nullable=False)


sales = sa.Table(
    "sales_invoice",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("terminal_id"),
    sa.Column("invoice_number", sa.String(50), nullable=False),
    money("gross_total"),
    money("discount"),
    money("taxable_total"),
    money("cgst"),
    money("sgst"),
    money("total"),
    *actor_columns(),
    timestamp(),
    scoped("terminal", "terminal_id"),
    sa.UniqueConstraint("business_id", "store_id", "id"),
    sa.UniqueConstraint("business_id", "invoice_number"),
    sa.CheckConstraint("total > 0 AND gross_total >= total AND discount = gross_total - total"),
    sa.CheckConstraint(
        "taxable_total >= 0 AND cgst >= 0 AND sgst >= 0 AND total = taxable_total + cgst + sgst"
    ),
    *command_constraints(),
    sa.Index("ix_sales_store_date", "business_id", "store_id", "created_at"),
)
items = sa.Table(
    "sales_item",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("sale_id"),
    uid("product_id"),
    sa.Column("line_number", sa.Integer, nullable=False),
    sa.Column("product_name", sa.String(180), nullable=False),
    sa.Column("sku", sa.String(64), nullable=False),
    sa.Column("unit", sa.String(16), nullable=False),
    sa.Column("hsn", sa.String(8)),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    money("unit_price"),
    money("gross"),
    money("discount"),
    money("taxable"),
    money("cgst"),
    money("sgst"),
    money("total"),
    sa.Column("gst_rate", sa.Numeric(5, 2), nullable=False),
    scoped("sales_invoice", "sale_id"),
    sa.ForeignKeyConstraint(["business_id", "product_id"], ["product.business_id", "product.id"]),
    sa.UniqueConstraint("business_id", "store_id", "product_id", "id"),
    sa.UniqueConstraint("business_id", "sale_id", "line_number"),
    sa.CheckConstraint("quantity > 0 AND unit_price >= 0 AND discount >= 0 AND gross >= discount"),
    sa.CheckConstraint(
        "total = gross - discount AND total = taxable + cgst + sgst AND gst_rate BETWEEN 0 AND 100"
    ),
)
allocations = sa.Table(
    "sales_stock_allocation",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("product_id"),
    uid("sale_item_id"),
    uid("batch_id"),
    uid("movement_id"),
    sa.Column("quantity", sa.Numeric(15, 3), nullable=False),
    sa.Column("unit_cost", sa.Numeric(18, 6), nullable=False),
    scoped("sales_item", "product_id", "sale_item_id"),
    scoped("stock_batch", "product_id", "batch_id"),
    scoped("stock_movement", "product_id", "movement_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint("quantity > 0 AND unit_cost >= 0"),
)
payments = sa.Table(
    "sales_payment",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("sale_id"),
    uid("account_id"),
    uid("movement_id"),
    sa.Column("method", sa.String(8), nullable=False),
    money("amount"),
    scoped("sales_invoice", "sale_id"),
    scoped("financial_movement", "account_id", "movement_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.CheckConstraint("amount > 0 AND method IN ('cash', 'upi', 'card')"),
)
supplier_payments = sa.Table(
    "supplier_payment",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("purchase_id"),
    uid("account_id"),
    uid("movement_id"),
    sa.Column("supplier_id", sa.Uuid(as_uuid=False), nullable=False),
    sa.Column("reference", sa.String(100), nullable=False),
    sa.Column("reference_identity", sa.String(100), nullable=False),
    sa.Column("reason", sa.String(500), nullable=False),
    money("amount"),
    *actor_columns(),
    timestamp(),
    scoped("purchase", "purchase_id"),
    scoped("financial_movement", "account_id", "movement_id"),
    sa.UniqueConstraint("business_id", "movement_id"),
    sa.UniqueConstraint("business_id", "supplier_id", "reference_identity"),
    sa.CheckConstraint("amount > 0"),
    *command_constraints(),
    sa.Index("ix_supplier_payment_purchase", "business_id", "store_id", "purchase_id"),
)
TABLE_NAMES = (
    "sales_invoice",
    "sales_item",
    "sales_stock_allocation",
    "sales_payment",
    "supplier_payment",
)
