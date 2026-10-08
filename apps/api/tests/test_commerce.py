"""Real stock, cash and payable integration checks; all source examples synthetic."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from fastapi import HTTPException
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from test_catalog import create_product, opening_payload
from test_finance import account, opening, post, setup
from test_identity import register
from test_purchases import payload as purchase_payload

from supermarket.commerce import SaleInput, calculate
from supermarket.commerce_models import sales, supplier_payments
from supermarket.identity import set_context

pytestmark = pytest.mark.database


def checkout(product, terminal, account_id, session_id, **changes):
    return {
        "terminal_id": terminal,
        "lines": [
            {
                "product_id": product["id"],
                "quantity": "2",
                "expected_price": "25.00",
                "discount": "0",
            }
        ],
        "bill_discount": "0",
        "payments": [
            {
                "account_id": account_id,
                "cash_session_id": session_id,
                "method": "cash",
                "amount": "50.00",
            }
        ],
        "confirmed": True,
        **changes,
    }


def prepared(client):
    headers, base, path, terminal = setup(client)
    product = create_product(client, base, headers)
    assert (
        post(client, path + "/opening-stock", headers, opening_payload(product["id"])).status_code
        == 201
    )
    cash = account(client, path, headers, terminal)
    session = post(client, path + "/cash-sessions", headers, opening(cash["id"])).json()["id"]
    return headers, base, path, terminal, product, cash, session


def test_checkout_is_atomic_replayable_and_reprintable(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        data = checkout(product, terminal, cash["id"], session)
        preview = client.post(
            path + "/sales/preview", headers=headers, json={**data, "confirmed": False}
        )
        assert preview.status_code == 200, preview.text
        assert Decimal(preview.json()["total"]) == 50
        assert client.get(path + "/sales").json() == []
        key = str(uuid4())
        result = post(client, path + "/sales", headers, data, key)
        assert result.status_code == 201, result.text
        assert post(client, path + "/sales", headers, data, key).json() == result.json()
        assert client.get(path + "/sales/" + result.json()["id"]).json() == result.json()
        assert len(client.get(path + "/sales").json()) == 1
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 8
        assert Decimal(client.get(path + "/cash-sessions").json()[0]["expected_cash"]) == 1050
        assert (
            post(client, path + "/sales", headers, {**data, "bill_discount": "1"}, key).status_code
            == 409
        )
        assert post(client, path + "/sales", headers, {**data, "payments": []}).status_code == 422
        assert (
            post(
                client,
                path + "/sales",
                headers,
                {
                    **data,
                    "lines": [{**data["lines"][0], "quantity": "20"}],
                    "payments": [{**data["payments"][0], "amount": "500"}],
                },
            ).status_code
            == 409
        )
        assert len(client.get(path + "/sales").json()) == 1
        assert (
            len(
                [
                    row
                    for row in client.get(base + "/audit").json()
                    if row["action"] == "sale.posted"
                ]
            )
            == 1
        )


def test_supplier_partial_payments_and_overpayment(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()["id"]
        product = create_product(client, base, headers)
        purchase = post(
            client, path + "/purchases", headers, purchase_payload(supplier, product["id"])
        ).json()
        bank = account(client, path, headers, amount="1000", name="DEMO bank")
        data = {
            "purchase_id": purchase["id"],
            "account_id": bank["id"],
            "amount": "200",
            "reference": "DEMO-PAY-1",
            "reason": "Synthetic partial supplier payment",
            "confirmed": True,
        }
        key = str(uuid4())
        result = post(client, path + "/supplier-payments", headers, data, key)
        assert result.status_code == 201, result.text
        assert post(client, path + "/supplier-payments", headers, data, key).json() == result.json()
        payable = client.get(path + "/supplier-payables").json()[0]
        assert Decimal(payable["outstanding"]) == 304
        assert Decimal(client.get(path + "/accounts").json()[0]["balance"]) == 800
        assert post(client, path + "/supplier-payments", headers, data).status_code == 409
        assert (
            post(
                client,
                path + "/supplier-payments",
                headers,
                {**data, "reference": "DEMO-PAY-2", "amount": "305"},
            ).status_code
            == 409
        )
        assert len(client.get(path + "/supplier-payments").json()) == 1
        final = post(
            client,
            path + "/supplier-payments",
            headers,
            {**data, "reference": "DEMO-PAY-2", "amount": "304"},
        )
        assert final.status_code == 201, final.text
        assert Decimal(client.get(path + "/supplier-payables").json()[0]["outstanding"]) == 0


def test_split_tenders_discounts_expiry_and_failures(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        upi = post(
            client,
            path + "/accounts",
            headers,
            {
                "name": "DEMO UPI",
                "kind": "upi",
                "opening_amount": "0",
                "reason": "Synthetic UPI account",
                "confirmed": True,
            },
        ).json()
        second = create_product(
            client,
            base,
            headers,
            sku="DEMO-2",
            name="Synthetic rice",
            unit="kg",
            selling_price="60",
            mrp="60",
            minimum_selling_price="0",
            gst_rate="0",
        )
        assert (
            post(
                client,
                path + "/opening-stock",
                headers,
                opening_payload(second["id"], quantity="20", unit_cost="45"),
            ).status_code
            == 201
        )
        data = checkout(
            product,
            terminal,
            cash["id"],
            session,
            lines=[
                {
                    "product_id": product["id"],
                    "quantity": "3",
                    "expected_price": "25",
                    "discount": "0.01",
                },
                {
                    "product_id": second["id"],
                    "quantity": "2.5",
                    "expected_price": "60",
                    "discount": "0",
                },
            ],
            bill_discount="1.00",
            payments=[
                {
                    "account_id": cash["id"],
                    "cash_session_id": session,
                    "method": "cash",
                    "amount": "100",
                },
                {"account_id": upi["id"], "method": "upi", "amount": "123.99"},
            ],
        )
        result = post(client, path + "/sales", headers, data)
        assert result.status_code == 201, result.text
        stored = result.json()
        assert Decimal(stored["discount"]) == Decimal("1.01")
        assert sum(Decimal(line["total"]) for line in stored["lines"]) == Decimal("223.99")
        assert Decimal(stored["total"]) == sum(
            Decimal(stored[field]) for field in ("taxable_total", "cgst", "sgst")
        )
        for bad in [
            {**data, "confirmed": False},
            {**data, "lines": [{**data["lines"][0], "expected_price": "24"}]},
            {**data, "lines": [{**data["lines"][0], "quantity": "1.5"}]},
            {**data, "payments": [{**data["payments"][0], "method": "card", "amount": "223.99"}]},
            {
                **data,
                "payments": [{**data["payments"][0], "cash_session_id": None, "amount": "223.99"}],
            },
            {
                **data,
                "payments": [
                    {**data["payments"][1], "cash_session_id": session, "amount": "223.99"}
                ],
            },
            {**data, "terminal_id": str(uuid4())},
        ]:
            assert post(client, path + "/sales", headers, bad).status_code in {404, 409, 422}
        expired = create_product(
            client,
            base,
            headers,
            sku="DEMO-EXP",
            name="Synthetic expired milk",
            batch_tracking=True,
            expiry_tracking=True,
        )
        assert (
            post(
                client,
                path + "/opening-stock",
                headers,
                opening_payload(
                    expired["id"],
                    batch_number="DEMO-LOT",
                    expiry_date=str(date.today() - timedelta(days=2)),
                ),
            ).status_code
            == 201
        )
        assert (
            post(
                client, path + "/sales", headers, checkout(expired, terminal, cash["id"], session)
            ).status_code
            == 409
        )
        assert (
            post(
                client,
                path + "/cash-sessions/" + session + "/close",
                headers,
                {
                    "expected_cash": "1100",
                    "actual_cash": "1100",
                    "reason": "Synthetic closing cash",
                    "confirmed": True,
                },
            ).status_code
            == 201
        )
        assert (
            post(
                client, path + "/sales", headers, checkout(product, terminal, cash["id"], session)
            ).status_code
            == 409
        )
        assert len(client.get(path + "/sales").json()) == 1
        assert client.get(path + "/sales/" + str(uuid4())).status_code == 404


def test_concurrent_sales_cannot_oversell_and_payments_cannot_overpay(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        data = checkout(
            product,
            terminal,
            cash["id"],
            session,
            lines=[
                {
                    "product_id": product["id"],
                    "quantity": "6",
                    "expected_price": "25",
                    "discount": "0",
                }
            ],
            payments=[
                {
                    "account_id": cash["id"],
                    "cash_session_id": session,
                    "method": "cash",
                    "amount": "150",
                }
            ],
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: post(client, path + "/sales", headers, data), range(2))
            )
        assert sorted(result.status_code for result in results) == [201, 409]
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 4
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()["id"]
        purchase = post(
            client, path + "/purchases", headers, purchase_payload(supplier, product["id"])
        ).json()
        pay = {
            "purchase_id": purchase["id"],
            "account_id": cash["id"],
            "cash_session_id": session,
            "amount": "300",
            "reference": "DEMO-concurrent",
            "reason": "Synthetic payment",
            "confirmed": True,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda number: post(
                        client,
                        path + "/supplier-payments",
                        headers,
                        {**pay, "reference": "DEMO-concurrent-" + str(number)},
                    ),
                    range(2),
                )
            )
        assert sorted(result.status_code for result in results) == [201, 409]
        assert Decimal(client.get(path + "/supplier-payables").json()[0]["outstanding"]) == 204


def test_commerce_permissions_and_cross_tenant_access(postgres_case: PostgreSQLCase):
    with (
        postgres_case.client() as owner,
        postgres_case.client() as cashier,
        postgres_case.client() as outsider,
    ):
        headers, base, path, terminal, product, cash, session = prepared(owner)
        cashier_headers = register(cashier, "cashier@example.com")
        outside_headers = register(outsider, "outside@example.com")
        assert (
            owner.post(
                base + "/members",
                headers=headers,
                json={
                    "email": "cashier@example.com",
                    "role": "CASHIER",
                    "store_ids": [path.split("/")[-1]],
                },
            ).status_code
            == 201
        )
        data = checkout(product, terminal, cash["id"], session)
        assert post(cashier, path + "/sales", cashier_headers, data).status_code == 403
        for endpoint in ("/sales", "/supplier-payables", "/supplier-payments"):
            assert outsider.get(path + endpoint).status_code == 404
        assert post(outsider, path + "/sales", outside_headers, data).status_code == 404
        assert cashier.get(path + "/supplier-payables").status_code == 403
        assert cashier.get(path + "/supplier-payments").status_code == 403
        badpay = {
            "purchase_id": str(uuid4()),
            "account_id": cash["id"],
            "amount": "10",
            "reference": "DEMO-permissions",
            "reason": "Synthetic payment",
            "confirmed": True,
        }
        assert (
            post(cashier, path + "/supplier-payments", cashier_headers, badpay).status_code == 403
        )
        assert post(owner, path + "/supplier-payments", headers, badpay).status_code == 404
        assert post(owner, path + "/sales", {}, data).status_code == 403
        wrongstore = owner.post(
            base + "/stores", headers=headers, json={"name": "DEMO other store", "address": ""}
        ).json()["id"]
        result = post(owner, path + "/sales", headers, data)
        assert result.status_code == 201
        assert (
            owner.get(base + "/stores/" + wrongstore + "/sales/" + result.json()["id"]).status_code
            == 404
        )
        assert (
            post(
                owner,
                path + "/sales",
                headers,
                {**data, "payments": [{**data["payments"][0], "account_id": str(uuid4())}]},
            ).status_code
            == 404
        )
        with postgres_case.runtime.begin() as connection:
            set_context(connection, None, None)
            assert connection.execute(select(sales)).all() == []
            assert connection.execute(select(supplier_payments)).all() == []
        for table in (
            "sales_invoice",
            "sales_item",
            "sales_stock_allocation",
            "sales_payment",
            "supplier_payment",
        ):
            with pytest.raises(DBAPIError), postgres_case.runtime.begin() as connection:
                set_context(connection, None, base.split("/")[-1])
                connection.execute(text("DELETE FROM " + table))
        with pytest.raises(DBAPIError), postgres_case.admin.begin() as connection:
            connection.execute(text("UPDATE sales_invoice SET total=total"))
        with pytest.raises(DBAPIError), postgres_case.admin.begin() as connection:
            record = dict(connection.execute(select(sales)).mappings().one())
            record.update(
                id=str(uuid4()), invoice_number="DEMO-INCOMPLETE", idempotency_key=str(uuid4())
            )
            connection.execute(insert(sales).values(**record))


def test_supplier_payment_insufficient_funds_replay_and_validation(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        product = create_product(client, base, headers)
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()["id"]
        purchase = post(
            client, path + "/purchases", headers, purchase_payload(supplier, product["id"])
        ).json()
        bank = account(client, path, headers, amount="50", name="DEMO bank")
        data = {
            "purchase_id": purchase["id"],
            "account_id": bank["id"],
            "amount": "60",
            "reference": "DEMO-FAIL",
            "reason": "Synthetic payment",
            "confirmed": True,
        }
        assert post(client, path + "/supplier-payments", headers, data).status_code == 422
        assert client.get(path + "/supplier-payments").json() == []
        for bad in [
            {**data, "confirmed": False},
            {**data, "amount": "0"},
            {**data, "amount": 10},
            {**data, "amount": "10", "cash_session_id": str(uuid4())},
        ]:
            assert post(client, path + "/supplier-payments", headers, bad).status_code == 422
        data["amount"] = "50"
        key = str(uuid4())
        assert post(client, path + "/supplier-payments", headers, data, key).status_code == 201
        assert (
            post(
                client,
                path + "/supplier-payments",
                headers,
                {**data, "reason": "Changed details"},
                key,
            ).status_code
            == 409
        )
        assert client.get(path + "/supplier-payments", params={"offset": 1}).json() == []
        assert client.get(path + "/supplier-payables", params={"offset": 1}).json() == []


def test_quote_paise_allocation_and_input_guards():
    ids = [str(uuid4()), str(uuid4()), str(uuid4())]
    terminal = str(uuid4())
    catalog = {
        identity: {
            "id": identity,
            "name": "Synthetic",
            "sku": "DEMO",
            "unit": "kg",
            "active": True,
            "hsn": None,
            "selling_price": Decimal("1"),
            "minimum_selling_price": Decimal("0"),
            "gst_rate": Decimal("5"),
        }
        for identity in ids
    }
    data = {
        "terminal_id": terminal,
        "lines": [
            {"product_id": identity, "quantity": "1", "expected_price": "1"} for identity in ids
        ],
        "bill_discount": "0.01",
    }
    quote = calculate(SaleInput.model_validate(data), catalog)
    assert quote.total == Decimal("2.99")
    assert [line.discount for line in quote.lines] == [Decimal("0.01"), Decimal("0"), Decimal("0")]
    for change in [
        {"bill_discount": "3"},
        {
            "lines": [
                {"product_id": ids[0], "quantity": "1", "expected_price": "1", "discount": "2"}
            ]
        },
    ]:
        with pytest.raises(HTTPException):
            calculate(SaleInput.model_validate({**data, **change}), catalog)
    catalog[ids[0]]["minimum_selling_price"] = Decimal("1")
    with pytest.raises(HTTPException):
        calculate(SaleInput.model_validate(data), catalog)
    for lines in [[data["lines"][0], data["lines"][0]], [{**data["lines"][0], "quantity": "0"}]]:
        with pytest.raises(ValueError):
            SaleInput.model_validate({**data, "lines": lines})
    with pytest.raises(ValueError):
        SaleInput.model_validate(
            {**data, "payments": [{"account_id": ids[0], "method": "cash", "amount": "0"}]}
        )
    with pytest.raises(ValueError):
        SaleInput.model_validate(
            {**data, "payments": [{"account_id": ids[0], "method": "cash", "amount": "1"}] * 2}
        )
