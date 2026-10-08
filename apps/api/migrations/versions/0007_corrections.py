"""Immutable credit notes, approved reversals and counted-stock corrections."""

from alembic import op
from sqlalchemy import text

from supermarket.schema_v0007 import TABLE_NAMES, metadata

revision = "0007_corrections"
down_revision = "0006_pos_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("supplier_payment", "sales_stock_allocation", "purchase_item"):
        op.create_unique_constraint("uq_" + name + "_business", name, ["business_id", "id"])
    for table in metadata.sorted_tables:
        if table.name in TABLE_NAMES:
            table.create(op.get_bind())
    for name in TABLE_NAMES:
        op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
        scope = "NULLIF(current_setting('app.business_id', true), '')::uuid"
        op.execute(
            f'CREATE POLICY tenant_scope ON "{name}" USING (business_id = {scope}) '
            f"WITH CHECK (business_id = {scope})"
        )
        op.execute(
            f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON "{name}" '
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"
        )
    constraint = (
        op.get_bind()
        .execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE "
                "conrelid='financial_movement'::regclass AND "
                "conname='ck_money_kind'"
            )
        )
        .scalar_one()
    )
    op.drop_constraint("ck_money_kind", "financial_movement", type_="check")
    op.create_check_constraint(
        "ck_money_kind",
        "financial_movement",
        constraint[7:-1]
        + (
            "OR (kind = 'sales_refund' AND amount < 0) OR (kind = "
            "'supplier_payment_reversal' AND amount > 0)"
        ),
    )
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_supplier_payment() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE invoice_amount numeric; supplier uuid; paid numeric; reversed numeric;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'purchase-payment:' || NEW.business_id::text || NEW.purchase_id::text, 0));
        SELECT invoice_total, supplier_id INTO invoice_amount, supplier FROM purchase
          WHERE id = NEW.purchase_id AND business_id = NEW.business_id AND store_id = NEW.store_id;
        SELECT COALESCE(sum(amount), 0) INTO paid FROM supplier_payment
          WHERE business_id = NEW.business_id AND purchase_id = NEW.purchase_id;
        SELECT COALESCE(sum(r.total),0) INTO reversed FROM supplier_payment_reversal r
          JOIN supplier_payment p ON p.id=r.target_id AND p.business_id=r.business_id
          WHERE p.business_id=NEW.business_id AND p.purchase_id=NEW.purchase_id;
        IF supplier IS DISTINCT FROM NEW.supplier_id OR NEW.amount > invoice_amount - paid +
          reversed
          OR EXISTS(SELECT 1 FROM purchase_reversal WHERE business_id=NEW.business_id AND
          target_id=NEW.purchase_id) THEN
          RAISE EXCEPTION 'Invalid invoice allocation' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM financial_movement WHERE id=NEW.movement_id
          AND business_id=NEW.business_id AND store_id=NEW.store_id AND account_id=NEW.account_id
          AND amount=-NEW.amount AND kind='supplier_payment' AND resource_id=NEW.id) THEN
          RAISE EXCEPTION 'Supplier payment ledger mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE FUNCTION guard_credit_item() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE sold numeric; returned numeric;
      BEGIN
        SELECT quantity INTO sold FROM sales_item WHERE id=NEW.sale_item_id
          AND business_id=NEW.business_id AND store_id=NEW.store_id AND product_id=NEW.product_id;
        PERFORM pg_advisory_xact_lock(hashtextextended('return-item:' || NEW.sale_item_id::text,0));
        SELECT COALESCE(sum(quantity),0) INTO returned FROM credit_item
          WHERE sale_item_id=NEW.sale_item_id AND business_id=NEW.business_id;
        IF sold IS NULL OR returned + NEW.quantity > sold OR NOT EXISTS(
          SELECT 1 FROM sales_item i JOIN credit_note n ON n.target_id=i.sale_id
            AND n.business_id=i.business_id
          WHERE i.id=NEW.sale_item_id AND n.id=NEW.credit_id
            AND n.business_id=NEW.business_id) THEN
          RAISE EXCEPTION 'Return exceeds sold quantity' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_credit_item BEFORE INSERT ON credit_item
        FOR EACH ROW EXECUTE FUNCTION guard_credit_item();
      CREATE FUNCTION check_complete_credit() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.total <> (SELECT COALESCE(sum(amount),0) FROM credit_refund WHERE
          credit_id=NEW.id AND business_id=NEW.business_id)
          OR NEW.total <> (SELECT COALESCE(sum(total),0) FROM credit_item WHERE credit_id=NEW.id
          AND business_id=NEW.business_id)
          OR EXISTS(SELECT 1 FROM credit_item i WHERE i.credit_id=NEW.id AND
          i.business_id=NEW.business_id
            AND i.quantity <> (SELECT COALESCE(sum(quantity),0) FROM credit_stock_allocation a
              WHERE a.credit_item_id=i.id AND a.business_id=i.business_id))
          OR EXISTS(SELECT 1 FROM credit_refund r JOIN financial_movement m ON
          m.id=r.movement_id AND m.business_id=r.business_id
            WHERE r.credit_id=NEW.id AND r.business_id=NEW.business_id
            AND (m.amount <> -r.amount OR m.kind <> 'sales_refund' OR m.resource_id <> r.id)) THEN
          RAISE EXCEPTION 'Incomplete credit ledger' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE CONSTRAINT TRIGGER complete_credit AFTER INSERT ON credit_note
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_complete_credit();
    """)
    op.create_index("ix_stock_batch_ledger", "stock_movement", ["business_id", "batch_id"])

    op.execute("""
      CREATE FUNCTION check_credit_stock() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM sales_stock_allocation a
          JOIN credit_item i ON i.sale_item_id=a.sale_item_id AND i.business_id=a.business_id
          JOIN stock_movement m ON m.id=NEW.movement_id AND m.business_id=a.business_id
          WHERE a.id=NEW.original_allocation_id AND i.id=NEW.credit_item_id
            AND a.business_id=NEW.business_id AND m.batch_id=a.batch_id
            AND m.product_id=a.product_id AND m.quantity=NEW.quantity
            AND m.unit_cost=a.unit_cost AND m.kind='sales_return'
            AND ((i.disposition='restock' AND NEW.damage_movement_id IS NULL)
              OR (i.disposition='discard' AND EXISTS(SELECT 1 FROM stock_movement d
                WHERE d.id=NEW.damage_movement_id AND d.business_id=a.business_id
                  AND d.batch_id=a.batch_id AND d.product_id=a.product_id
                  AND d.quantity=-NEW.quantity AND d.unit_cost=a.unit_cost AND d.kind='damage')))
        ) OR NEW.quantity + (SELECT COALESCE(sum(quantity),0) FROM credit_stock_allocation
            WHERE original_allocation_id=NEW.original_allocation_id
              AND business_id=NEW.business_id) > (SELECT quantity FROM sales_stock_allocation
              WHERE id=NEW.original_allocation_id AND business_id=NEW.business_id) THEN
          RAISE EXCEPTION 'Credit stock source mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_credit_stock BEFORE INSERT ON credit_stock_allocation
        FOR EACH ROW EXECUTE FUNCTION check_credit_stock();
      CREATE FUNCTION check_payment_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM supplier_payment p JOIN financial_movement m
          ON m.id=NEW.movement_id AND m.business_id=p.business_id
          WHERE p.id=NEW.target_id AND p.business_id=NEW.business_id
            AND p.amount=NEW.total AND p.account_id=NEW.account_id
            AND m.account_id=p.account_id AND m.amount=p.amount
            AND m.resource_id=NEW.id AND m.kind='supplier_payment_reversal') THEN
          RAISE EXCEPTION 'Payment reversal ledger mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_payment_reversal BEFORE INSERT ON supplier_payment_reversal
        FOR EACH ROW EXECUTE FUNCTION check_payment_reversal();
    """)


def downgrade() -> None:
    if any(
        op.get_bind()
        .execute(
            text('SELECT EXISTS(SELECT 1 FROM "' + name + '")')  # noqa: S608
        )
        .scalar()
        for name in TABLE_NAMES
    ):
        raise RuntimeError("Posted corrections require forward recovery, not downgrade")
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())
    op.execute("DROP FUNCTION check_complete_credit()")
    op.execute("DROP FUNCTION guard_credit_item()")
    op.execute("DROP FUNCTION check_credit_stock()")
    op.execute("DROP FUNCTION check_payment_reversal()")
    op.drop_index("ix_stock_batch_ledger", "stock_movement")
    for name in ("supplier_payment", "sales_stock_allocation", "purchase_item"):
        op.drop_constraint("uq_" + name + "_business", name, type_="unique")
    op.drop_constraint("ck_money_kind", "financial_movement", type_="check")
    op.create_check_constraint(
        "ck_money_kind",
        "financial_movement",
        "(kind='opening' AND amount>=0 AND cash_session_id IS NULL) OR "
        "(kind='opening_variance' AND amount<>0 AND cash_session_id IS NULL) OR "
        "(kind IN ('expense','withdrawal','supplier_payment') AND amount<0) OR "
        "(kind IN ('receipt','expense_reversal','sale') AND amount>0) OR "
        "(kind='cash_variance' AND amount<>0 AND cash_session_id IS NOT NULL)",
    )
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_supplier_payment() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE invoice_amount numeric; supplier uuid; paid numeric;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'purchase-payment:' || NEW.business_id::text || NEW.purchase_id::text,0));
        SELECT invoice_total,supplier_id INTO invoice_amount,supplier FROM purchase
          WHERE id=NEW.purchase_id AND business_id=NEW.business_id AND store_id=NEW.store_id;
        SELECT COALESCE(sum(amount),0) INTO paid FROM supplier_payment
          WHERE business_id=NEW.business_id AND purchase_id=NEW.purchase_id;
        IF supplier IS DISTINCT FROM NEW.supplier_id OR NEW.amount > invoice_amount-paid THEN
          RAISE EXCEPTION 'Invalid invoice allocation' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM financial_movement WHERE id=NEW.movement_id
          AND business_id=NEW.business_id AND store_id=NEW.store_id AND account_id=NEW.account_id
          AND amount=-NEW.amount AND kind='supplier_payment' AND resource_id=NEW.id) THEN
          RAISE EXCEPTION 'Supplier payment ledger mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$;
    """)
