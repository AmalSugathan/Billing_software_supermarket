"""Paid expenses and immutable cashier cash reconciliation."""

from alembic import op
from sqlalchemy import text

from supermarket.schema_v0005 import TABLE_NAMES, metadata

revision = "0005_finance_cash"
down_revision = "0004_purchases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_terminal_store_scope", "terminal", ["business_id", "store_id", "id"]
    )
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
    op.execute(
        "CREATE UNIQUE INDEX one_account_opening ON financial_movement "
        "(business_id, account_id) WHERE kind = 'opening'"
    )
    op.execute("""
      CREATE FUNCTION guard_cash_session() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE account_kind text; current_balance numeric;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'cashier:' || NEW.business_id::text || NEW.actor_user_id::text, 0));
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'money-account:' || NEW.business_id::text || NEW.account_id::text, 0));
        SELECT kind INTO account_kind FROM financial_account
          WHERE id = NEW.account_id AND business_id = NEW.business_id AND store_id = NEW.store_id;
        IF account_kind IS DISTINCT FROM 'cash' THEN
          RAISE EXCEPTION 'Cash drawer required' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM cash_session s WHERE s.business_id = NEW.business_id
          AND (s.account_id = NEW.account_id OR s.actor_user_id = NEW.actor_user_id)
          AND NOT EXISTS (SELECT 1 FROM cash_closing c
            WHERE c.business_id = s.business_id AND c.cash_session_id = s.id)) THEN
          RAISE EXCEPTION 'Drawer already open' USING ERRCODE = '23505';
        END IF;
        SELECT COALESCE(sum(amount), 0) INTO current_balance FROM financial_movement
          WHERE business_id = NEW.business_id AND account_id = NEW.account_id;
        IF current_balance <> NEW.opening_cash THEN
          RAISE EXCEPTION 'Opening count mismatch' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_cash_session BEFORE INSERT ON cash_session
        FOR EACH ROW EXECUTE FUNCTION guard_cash_session();
    """)
    op.execute("""
      CREATE FUNCTION guard_financial_movement() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE account_kind text; current_balance numeric;
      BEGIN
        PERFORM pg_advisory_xact_lock(hashtextextended(
          'money-account:' || NEW.business_id::text || NEW.account_id::text, 0));
        SELECT kind INTO account_kind FROM financial_account WHERE id = NEW.account_id
          AND business_id = NEW.business_id AND store_id = NEW.store_id;
        IF account_kind IS NULL THEN
          RAISE EXCEPTION 'Account unavailable' USING ERRCODE = '23503';
        END IF;
        IF account_kind <> 'cash' AND NEW.cash_session_id IS NOT NULL THEN
          RAISE EXCEPTION 'Noncash session' USING ERRCODE = '23514';
        END IF;
        IF NEW.kind = 'opening_variance' THEN
          IF account_kind <> 'cash' OR EXISTS (SELECT 1 FROM cash_session s
            WHERE s.business_id = NEW.business_id AND s.account_id = NEW.account_id
              AND NOT EXISTS (SELECT 1 FROM cash_closing c WHERE c.cash_session_id = s.id)) THEN
            RAISE EXCEPTION 'Closed drawer required' USING ERRCODE = '23514';
          END IF;
        ELSIF account_kind = 'cash' AND NEW.kind <> 'opening' THEN
          IF NEW.cash_session_id IS NULL OR NOT EXISTS (SELECT 1 FROM cash_session s
            WHERE s.id = NEW.cash_session_id AND s.business_id = NEW.business_id
              AND s.account_id = NEW.account_id AND NOT EXISTS (SELECT 1 FROM cash_closing c
                WHERE c.business_id = s.business_id AND c.cash_session_id = s.id)) THEN
            RAISE EXCEPTION 'Open drawer session required' USING ERRCODE = '23514';
          END IF;
        END IF;
        SELECT COALESCE(sum(amount), 0) INTO current_balance FROM financial_movement
          WHERE business_id = NEW.business_id AND account_id = NEW.account_id;
        IF current_balance + NEW.amount < 0 OR current_balance + NEW.amount > 999999999999.99 THEN
          RAISE EXCEPTION 'Account balance out of range' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END; $$;
      CREATE TRIGGER checked_financial_movement BEFORE INSERT ON financial_movement
        FOR EACH ROW EXECUTE FUNCTION guard_financial_movement();
    """)
    op.get_bind().execute(text("INSERT INTO permission (code) VALUES ('cash.sessions')"))
    op.execute(
        "INSERT INTO role_permission (business_id, role_id, "
        "permission_code) SELECT business_id, id, 'cash.sessions' FROM "
        "role WHERE name IN ('OWNER', 'STORE_MANAGER', 'CASHIER')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission_code = 'cash.sessions'")
    op.execute("DELETE FROM permission WHERE code = 'cash.sessions'")
    for table in reversed(metadata.sorted_tables):
        if table.name in TABLE_NAMES:
            table.drop(op.get_bind())
    op.execute("DROP FUNCTION guard_financial_movement()")
    op.execute("DROP FUNCTION guard_cash_session()")
    op.drop_constraint("uq_terminal_store_scope", "terminal", type_="unique")
