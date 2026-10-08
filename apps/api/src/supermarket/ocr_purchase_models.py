"""Reviewed source-to-purchase approval linkage; append-only and tenant scoped."""

import sqlalchemy as sa

from supermarket.catalog_models import timestamp, uid

metadata = sa.MetaData()
for name, columns in {
    "store": ["id", "business_id"],
    "user_account": ["id"],
    "purchase": ["id", "business_id", "store_id"],
    "ocr_document": ["id", "business_id", "store_id"],
    "ocr_attempt": ["id", "business_id", "store_id", "document_id"],
}.items():
    sa.Table(name, metadata, *(uid(c, primary_key=c == "id") for c in columns))

links = sa.Table(
    "ocr_purchase_link",
    metadata,
    uid(primary_key=True),
    uid("business_id"),
    uid("store_id"),
    uid("document_id"),
    uid("attempt_id"),
    uid("purchase_id"),
    uid("actor_user_id"),
    sa.Column("source_sha256", sa.String(64), nullable=False),
    sa.Column("review_reason", sa.String(500), nullable=False),
    sa.Column("human_approved", sa.Boolean, nullable=False),
    timestamp(),
    sa.CheckConstraint("human_approved"),
    sa.UniqueConstraint("business_id", "document_id"),
    sa.UniqueConstraint("business_id", "purchase_id"),
    sa.ForeignKeyConstraint(["actor_user_id"], ["user_account.id"]),
    sa.ForeignKeyConstraint(
        ["business_id", "store_id", "document_id", "attempt_id"],
        [
            "ocr_attempt.business_id",
            "ocr_attempt.store_id",
            "ocr_attempt.document_id",
            "ocr_attempt.id",
        ],
    ),
    *[
        sa.ForeignKeyConstraint(
            ["business_id", "store_id", column],
            [table + ".business_id", table + ".store_id", table + ".id"],
        )
        for column, table in (
            ("document_id", "ocr_document"),
            ("purchase_id", "purchase"),
        )
    ],
)
TABLE_NAMES = ("ocr_purchase_link",)
