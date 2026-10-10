"""Owner metrics reconcile against real synthetic postings and tenant permissions."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from sqlalchemy import text
from test_catalog import create_product, opening_payload
from test_commerce import checkout, prepared
from test_finance import account, expense, post, setup
from test_identity import business, register
from test_purchases import payload

pytestmark = pytest.mark.database


def report(client, path, **params):
    result = client.get(path + "/insights", params=params)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


def test_sales_return_costs_expenses_and_cash_variance_reconcile(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        initial = report(client, path)
        assert initial["metrics"]["opening_funds"] == "1000.00"
        assert initial["metrics"]["money_in"] == "0.00"
        assert initial["metrics"]["estimated_gross_profit"] == "0.00"
        sale = post(
            client, path + "/sales", headers, checkout(product, terminal, cash["id"], session)
        ).json()
        result = report(client, path)
        assert result["metrics"]["revenue"] == sale["taxable_total"]
        assert result["metrics"]["cogs"] == "37.00"
        assert result["metrics"]["money_in"] == "50.00"
        assert (
            Decimal(result["metrics"]["estimated_gross_profit"])
            == Decimal(sale["taxable_total"]) - 37
        )
        # Changing today's catalog cost cannot rewrite historical margins.
        with postgres_case.admin.begin() as connection:
            connection.execute(
                text("UPDATE product SET landed_cost=999 WHERE id=:id"), {"id": product["id"]}
            )
        assert report(client, path)["metrics"]["cogs"] == "37.00"
        credit = post(
            client,
            path + "/sales/" + sale["id"] + "/credits",
            headers,
            {
                "kind": "return",
                "reason": "Synthetic damaged packet return",
                "confirmed": True,
                "lines": [{"product_id": product["id"], "quantity": "1", "disposition": "discard"}],
                "payments": [
                    {
                        "account_id": cash["id"],
                        "cash_session_id": session,
                        "method": "cash",
                        "amount": "25",
                    }
                ],
            },
        )
        assert credit.status_code == 201, credit.text
        paid = post(client, path + "/expenses", headers, expense(cash["id"], session, amount="10"))
        assert paid.status_code == 201, paid.text
        before = report(client, path)
        assert before["metrics"]["cogs"] == "18.50"
        assert before["metrics"]["stock_loss"] == "18.50"
        assert before["metrics"]["money_out"] == "35.00"
        assert before["metrics"]["expenses"] == "10.00"
        reverse = post(
            client,
            path + "/expenses/" + paid.json()["id"] + "/reverse",
            headers,
            {"reason": "Synthetic expense reversal", "cash_session_id": session, "confirmed": True},
        )
        assert reverse.status_code == 201, reverse.text
        close = post(
            client,
            path + "/cash-sessions/" + session + "/close",
            headers,
            {
                "expected_cash": "1025",
                "actual_cash": "1020",
                "reason": "Synthetic count shortfall",
                "confirmed": True,
            },
        )
        assert close.status_code == 201, close.text
        final = report(client, path)
        assert final["metrics"]["expenses"] == "0.00"
        assert final["metrics"]["net_cash_flow"] == "25.00"
        assert final["metrics"]["cash_variance"] == "-5.00"
        assert final["metrics"]["variance_count"] == 1
        assert final["snapshot"]["recorded_balance"] == "1020.00"
        assert final["snapshot"]["inventory_value"] == "148.00"
        assert final["metrics"]["credit_count"] == 1
        assert final["daily"][0]["net_cash_flow"] == "25.00"
        assert final["briefing_method"] == "rules_based"
        audit_count = len(client.get(base + "/audit").json())
        assert report(client, path)["metrics"] == final["metrics"]
        assert len(client.get(base + "/audit").json()) == audit_count


def test_payables_reversals_and_historical_cutoff(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, _ = setup(client)
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()["id"]
        product = create_product(client, base, headers)
        purchase = post(
            client, path + "/purchases", headers, payload(supplier, product["id"])
        ).json()
        bank = account(client, path, headers, name="DEMO bank")
        payment = post(
            client,
            path + "/supplier-payments",
            headers,
            {
                "purchase_id": purchase["id"],
                "account_id": bank["id"],
                "amount": "200",
                "reference": "DEMO-insights",
                "reason": "Synthetic supplier payment",
                "confirmed": True,
            },
        ).json()
        result = report(client, path)
        assert result["snapshot"]["supplier_outstanding"] == "304.00"
        assert result["metrics"]["money_out"] == "200.00"
        assert result["metrics"]["cogs"] == "0.00"
        assert result["metrics"]["estimated_gross_profit"] == "0.00"
        reason = {"reason": "Synthetic report correction", "confirmed": True}
        assert (
            post(
                client, path + "/supplier-payments/" + payment["id"] + "/reverse", headers, reason
            ).status_code
            == 201
        )
        assert report(client, path)["snapshot"]["supplier_outstanding"] == "504.00"
        assert (
            post(
                client, path + "/purchases/" + purchase["id"] + "/reverse", headers, reason
            ).status_code
            == 201
        )
        assert report(client, path)["snapshot"]["supplier_outstanding"] == "0.00"
        yesterday = date.fromisoformat(result["end"]) - timedelta(days=1)
        previous = report(client, path, start=str(yesterday), end=str(yesterday))
        assert previous["snapshot"]["recorded_balance"] == "0.00"
        assert previous["snapshot"]["inventory_value"] == "0.00"
        assert previous["metrics"]["money_out"] == "0.00"


def test_stock_attention_and_empty_data(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, _ = setup(client)
        empty = report(client, path)
        today = date.fromisoformat(empty["end"])
        assert empty["snapshot"]["stocked_product_count"] == 0
        assert any("No posted sales" in item["title"] for item in empty["briefing"])
        product = create_product(client, base, headers, batch_tracking=True, expiry_tracking=True)
        for batch, expiry in [
            ("EXPIRED", today - timedelta(days=1)),
            ("SOON", today + timedelta(days=14)),
        ]:
            result = post(
                client,
                path + "/opening-stock",
                headers,
                opening_payload(
                    product["id"], quantity="1", batch_number=batch, expiry_date=str(expiry)
                ),
            )
            assert result.status_code == 201, result.text
        current = report(client, path)
        assert current["snapshot"]["low_stock_count"] == 1
        assert current["snapshot"]["expired_batch_count"] == 1
        assert current["snapshot"]["expiring_batch_count"] == 1
        assert client.get(path + "/insights?start=2020-01-01&end=2022-01-01").status_code == 422
        assert client.get(path + "/insights?start=bad").status_code == 422
        assert client.get(path + "/insights?end=9999-12-31").status_code == 422


def test_reporting_requires_finance_permission_and_correct_tenant_store(
    postgres_case: PostgreSQLCase,
):
    with (
        postgres_case.client() as owner,
        postgres_case.client() as outsider,
        postgres_case.client() as cashier,
    ):
        headers, base, path, _ = setup(owner)
        register(outsider, "outsider@example.com")
        foreign = business(
            outsider,
            {
                "Origin": "http://testserver",
                "X-CSRF-Token": outsider.get("/api/v1/auth/session").json()["csrf_token"],
            },
        )
        foreign_store = outsider.get("/api/v1/businesses/" + foreign + "/stores").json()[0]["id"]
        assert outsider.get(path + "/insights").status_code == 404
        assert owner.get(base + "/stores/" + foreign_store + "/insights").status_code == 404
        assert owner.get(base + "/stores/" + str(uuid4()) + "/insights").status_code == 404
        register(cashier, "cashier@example.com")
        granted = owner.post(
            base + "/members",
            headers=headers,
            json={
                "email": "cashier@example.com",
                "role": "CASHIER",
                "store_ids": [path.rsplit("/", 1)[1]],
            },
        )
        assert granted.status_code == 201, granted.text
        assert cashier.get(path + "/insights").status_code == 403
        cashier.cookies.clear()
        assert cashier.get(path + "/insights").status_code == 401
