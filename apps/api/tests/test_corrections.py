"""Real correction/reconciliation checks against synthetic stock and payments."""

from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from test_catalog import create_product
from test_commerce import checkout, prepared
from test_finance import account, post, setup
from test_purchases import payload as purchase_payload

pytestmark = pytest.mark.database


def test_partial_return_then_cancel_protection_and_exact_final_tax(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        sale = post(
            client, path + "/sales", headers, checkout(product, terminal, cash["id"], session)
        ).json()
        data = {
            "kind": "return",
            "reason": "Synthetic customer returned one packet",
            "confirmed": True,
            "lines": [{"product_id": product["id"], "quantity": "1", "disposition": "restock"}],
            "payments": [
                {
                    "account_id": cash["id"],
                    "cash_session_id": session,
                    "method": "cash",
                    "amount": "25",
                }
            ],
        }
        key = str(uuid4())
        first = post(client, path + "/sales/" + sale["id"] + "/credits", headers, data, key)
        assert first.status_code == 201, first.text
        assert (
            post(client, path + "/sales/" + sale["id"] + "/credits", headers, data, key).json()
            == first.json()
        )
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 9
        assert Decimal(client.get(path + "/cash-sessions").json()[0]["expected_cash"]) == 1025
        assert (
            post(
                client,
                path + "/sales/" + sale["id"] + "/credits",
                headers,
                {**data, "kind": "cancel"},
            ).status_code
            == 409
        )
        second = post(
            client,
            path + "/sales/" + sale["id"] + "/credits",
            headers,
            {**data, "lines": [{**data["lines"][0], "disposition": "discard"}]},
        )
        assert second.status_code == 201, second.text
        assert (
            post(client, path + "/sales/" + sale["id"] + "/credits", headers, data).status_code
            == 409
        )
        credits = client.get(path + "/sales/" + sale["id"] + "/credits").json()
        for field in ("total", "cgst", "sgst"):
            assert sum(
                Decimal(credit[field])
                if field in credit
                else sum(Decimal(line[field]) for line in credit["lines"])
                for credit in credits
            ) == Decimal(sale[field])
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 9
        assert Decimal(client.get(path + "/cash-sessions").json()[0]["expected_cash"]) == 1000
        assert len(client.get(path + "/corrections").json()) == 2


def test_supplier_payment_then_purchase_reversal_preserves_original(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        product = create_product(client, base, headers)
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()["id"]
        purchase = post(
            client, path + "/purchases", headers, purchase_payload(supplier, product["id"])
        ).json()
        bank = account(client, path, headers, amount="1000", name="DEMO bank")
        payment = post(
            client,
            path + "/supplier-payments",
            headers,
            {
                "purchase_id": purchase["id"],
                "account_id": bank["id"],
                "amount": "200",
                "reference": "DEMO-PAY",
                "reason": "Synthetic supplier payment",
                "confirmed": True,
            },
        ).json()
        reason = {"reason": "Synthetic posting correction", "confirmed": True}
        assert (
            post(
                client, path + "/purchases/" + purchase["id"] + "/reverse", headers, reason
            ).status_code
            == 409
        )
        reverse = post(
            client, path + "/supplier-payments/" + payment["id"] + "/reverse", headers, reason
        )
        assert reverse.status_code == 201, reverse.text
        assert Decimal(client.get(path + "/supplier-payables").json()[0]["outstanding"]) == 504
        assert Decimal(client.get(path + "/accounts").json()[0]["balance"]) == 1000
        assert (
            post(
                client, path + "/supplier-payments/" + payment["id"] + "/reverse", headers, reason
            ).status_code
            == 409
        )
        result = post(client, path + "/purchases/" + purchase["id"] + "/reverse", headers, reason)
        assert result.status_code == 201, result.text
        assert client.get(path + "/supplier-payables").json()[0]["reversed"] is True
        assert Decimal(client.get(path + "/supplier-payables").json()[0]["outstanding"]) == 0
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 0
        assert client.get(path + "/purchases/" + purchase["id"]).json() == purchase


def test_stock_count_adjustment_rejects_stale_count_and_keeps_delta(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        batch = client.get(path + "/stock-batches").json()[0]
        data = {
            "expected_count": "10",
            "actual_count": "8",
            "reason": "Synthetic physical count discrepancy",
            "confirmed": True,
        }
        result = post(client, path + "/stock-batches/" + batch["id"] + "/adjust", headers, data)
        assert result.status_code == 201, result.text
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 8
        assert (
            post(
                client, path + "/stock-batches/" + batch["id"] + "/adjust", headers, data
            ).status_code
            == 409
        )
        assert (
            post(
                client,
                path + "/stock-batches/" + batch["id"] + "/adjust",
                headers,
                {**data, "expected_count": "8", "actual_count": "9"},
            ).status_code
            == 422
        )
        assert (
            post(
                client,
                path + "/stock-batches/" + batch["id"] + "/adjust",
                headers,
                {**data, "expected_count": "8", "actual_count": "9", "added_unit_cost": "18.50"},
            ).status_code
            == 201
        )


def test_return_preview_permissions_and_concurrent_full_cancellation(postgres_case: PostgreSQLCase):
    from concurrent.futures import ThreadPoolExecutor

    from test_identity import register

    with (
        postgres_case.client() as owner,
        postgres_case.client() as cashier,
        postgres_case.client() as outsider,
    ):
        headers, base, path, terminal, product, cash, session = prepared(owner)
        staff = register(cashier, "staff@example.com")
        other = register(outsider, "outside@example.com")
        assert (
            owner.post(
                base + "/members",
                headers=headers,
                json={
                    "email": "staff@example.com",
                    "role": "CASHIER",
                    "store_ids": [path.split("/")[-1]],
                },
            ).status_code
            == 201
        )
        sale = post(
            owner, path + "/sales", headers, checkout(product, terminal, cash["id"], session)
        ).json()
        route = path + "/sales/" + sale["id"] + "/credits"
        data = {
            "kind": "cancel",
            "reason": "Synthetic cancellation review",
            "confirmed": True,
            "lines": [{"product_id": product["id"], "quantity": "2", "disposition": "restock"}],
            "payments": [
                {
                    "account_id": cash["id"],
                    "cash_session_id": session,
                    "method": "cash",
                    "amount": "50",
                }
            ],
        }
        preview = owner.post(route + "/preview", headers=headers, json=data)
        assert preview.status_code == 200, preview.text
        assert Decimal(preview.json()[0]["total"]) == 50
        assert owner.get(route).json() == []
        assert post(cashier, route, staff, data).status_code == 403
        assert post(outsider, route, other, data).status_code == 404
        assert cashier.get(path + "/corrections").status_code == 403
        assert outsider.get(path + "/stock-batches").status_code == 404
        assert post(owner, route, headers, {**data, "payments": []}).status_code == 422
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: post(owner, route, headers, data), range(2)))
        assert sorted(result.status_code for result in results) == [201, 409]
        assert Decimal(owner.get(path + "/stock").json()[0]["quantity"]) == 10
        assert Decimal(owner.get(path + "/cash-sessions").json()[0]["expected_cash"]) == 1000


def test_used_purchase_and_reserved_stock_cannot_be_reversed_or_counted_away(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()["id"]
        purchase = post(
            client, path + "/purchases", headers, purchase_payload(supplier, product["id"])
        ).json()
        batch_id = purchase["lines"][0]["batch_id"]
        adjusted = post(
            client,
            path + "/stock-batches/" + batch_id + "/adjust",
            headers,
            {
                "expected_count": "58",
                "actual_count": "57",
                "reason": "Synthetic missing purchase unit",
                "confirmed": True,
            },
        )
        assert adjusted.status_code == 201, adjusted.text
        assert (
            post(
                client,
                path + "/purchases/" + purchase["id"] + "/reverse",
                headers,
                {"reason": "Synthetic used purchase reversal", "confirmed": True},
            ).status_code
            == 409
        )
        lease = post(
            client,
            path + "/offline-leases",
            headers,
            {
                "terminal_id": terminal,
                "cash_session_id": session,
                "products": [{"product_id": product["id"], "quantity": "8"}],
                "reason": "Synthetic reserved stock",
                "confirmed": True,
            },
        )
        assert lease.status_code == 201
        # Untracked purchases share the opening lot; the reserved quantity still cannot be removed.
        result = post(
            client,
            path + "/stock-batches/" + batch_id + "/adjust",
            headers,
            {
                "expected_count": "57",
                "actual_count": "1",
                "reason": "Synthetic count conflicts with reserved till",
                "confirmed": True,
            },
        )
        assert result.status_code == 409, result.text
