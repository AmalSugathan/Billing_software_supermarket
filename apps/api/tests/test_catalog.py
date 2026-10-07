from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_identity import business, register

from supermarket.catalog_models import barcodes, batches, movements, products
from supermarket.identity import set_context

pytestmark = pytest.mark.database


def product_payload(**overrides):
    return {
        "sku": "GOOD-DAY-100",
        "name": "Good Day Cashew Biscuit 100g",
        "unit": "pcs",
        "purchase_price": "18.50",
        "landed_cost": "19.00",
        "selling_price": "25.00",
        "mrp": "30.00",
        "gst_rate": "5.00",
        "hsn": "1905",
        "reorder_level": "5",
        "reorder_quantity": "20",
        **overrides,
    }


def create_product(client: TestClient, base: str, headers: dict[str, str], **overrides):
    result = client.post(base + "/products", headers=headers, json=product_payload(**overrides))
    assert result.status_code == 201, result.text
    return result.json()


def store_id(client: TestClient, base: str) -> str:
    return client.get(base + "/stores").json()[0]["id"]


def opening_payload(product_id: str, **overrides):
    return {
        "product_id": product_id,
        "quantity": "10",
        "unit_cost": "18.50",
        "reason": "Reviewed physical opening count",
        "confirmed": True,
        **overrides,
    }


def test_catalog_creation_search_barcode_matches_and_atomic_duplicates(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        category = client.post(
            base + "/categories", headers=headers, json={"name": "Biscuits"}
        ).json()
        brand = client.post(base + "/brands", headers=headers, json={"name": "Britannia"}).json()
        assert (
            client.post(
                base + "/categories", headers=headers, json={"name": "Biscuits"}
            ).status_code
            == 409
        )
        assert client.get(base + "/categories").json()[0]["id"] == category["id"]
        assert client.get(base + "/brands").json()[0]["id"] == brand["id"]
        item = create_product(
            client,
            base,
            headers,
            category_id=category["id"],
            brand_id=brand["id"],
            barcodes=["890000001"],
            alternate_names=["Supplier biscuit", "Supplier biscuit"],
        )
        assert item["purchase_price"] == "18.50" and item["alternate_names"] == ["Supplier biscuit"]
        assert (
            client.get(base + "/products", params={"q": "Supplier biscuit"}).json()[0]["id"]
            == item["id"]
        )
        assert client.get(base + "/products", params={"q": "%"}).json() == []
        assert (
            client.get(base + "/products/resolve-barcode", params={"value": "890000001"}).json()[
                "id"
            ]
            == item["id"]
        )
        assert (
            client.get(base + "/products/resolve-barcode", params={"value": "unknown"}).status_code
            == 404
        )
        assert (
            client.get(
                base + "/products/matches", params={"name": "Good Day Cashew Biscuits 100g"}
            ).json()[0]["similarity"]
            >= 75
        )
        assert (
            client.post(
                base + "/products",
                headers=headers,
                json=product_payload(sku="another", name="Good Day Cashew Biscuits 100g"),
            ).status_code
            == 409
        )
        create_product(
            client,
            base,
            headers,
            sku="GD-200",
            name="Good Day Cashew Biscuit 200g",
            confirm_distinct_product=True,
        )
        assert (
            client.post(
                base + "/products",
                headers=headers,
                json=product_payload(
                    sku="GD-300", name="Other completely different item", barcodes=["890000001"]
                ),
            ).status_code
            == 409
        )
        assert len(client.get(base + "/products").json()) == 2
        assert (
            client.post(
                base + "/products",
                headers=headers,
                json=product_payload(sku="exact-name", confirm_distinct_product=True),
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + f"/products/{item['id']}/barcodes",
                headers=headers,
                json={"value": "extra-code"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                base + f"/products/{item['id']}/barcodes",
                headers=headers,
                json={"value": "extra-code"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + f"/products/{uuid4()}/barcodes",
                headers=headers,
                json={"value": "unused-code"},
            ).status_code
            == 404
        )
        assert client.get(base + "/products", params={"limit": 1, "offset": 1}).status_code == 200
        assert client.get(base + "/products", params={"limit": 201}).status_code == 422


def test_supplier_and_foreign_references_are_tenant_scoped(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        supplier = {
            "name": "Original invoice supplier",
            "gstin": "32ABCDE1234F1Z5",
            "payment_terms_days": 30,
        }
        result = client.post(base + "/suppliers", headers=headers, json=supplier)
        assert result.status_code == 201
        assert client.post(base + "/suppliers", headers=headers, json=supplier).status_code == 409
        assert client.get(base + "/suppliers").json()[0]["payment_terms_days"] == 30
        assert (
            client.post(
                base + "/suppliers", headers=headers, json={"name": "Bad", "gstin": "invalid"}
            ).status_code
            == 422
        )
        other = "/api/v1/businesses/" + business(client, headers, "Second business")
        category = client.post(
            other + "/categories", headers=headers, json={"name": "Other category"}
        ).json()["id"]
        for key, value in (
            ("category_id", category),
            ("brand_id", str(uuid4())),
            ("supplier_id", str(uuid4())),
        ):
            assert (
                client.post(
                    base + "/products", headers=headers, json=product_payload(**{key: value})
                ).status_code
                == 422
            )
        item = create_product(
            client,
            base,
            headers,
            supplier_id=result.json()["id"],
            barcodes=["same-in-other-business"],
        )
        second = create_product(client, other, headers, barcodes=["same-in-other-business"])
        assert item["id"] != second["id"]
        assert len(client.get(base + "/products").json()) == 1
        assert client.get(other + "/suppliers").json() == []
        with postgres_case.runtime.begin() as connection:
            assert connection.execute(select(products)).all() == []
            actor = client.get("/api/v1/auth/session").json()["user"]["id"]
            set_context(connection, actor, base.rsplit("/", 1)[1])
            assert len(connection.execute(select(products)).all()) == 1
        with pytest.raises(IntegrityError), postgres_case.admin.begin() as connection:
            connection.execute(
                insert(barcodes).values(
                    business_id=other.rsplit("/", 1)[1],
                    product_id=item["id"],
                    value="foreign-attack",
                )
            )


def test_opening_stock_confirmation_retries_immutability_and_reconciliation(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        item = create_product(client, base, headers)
        store = store_id(client, base)
        path = base + f"/stores/{store}/opening-stock"
        payload = opening_payload(item["id"])
        posting = {**headers, "Idempotency-Key": str(uuid4())}
        assert client.get(base + f"/stores/{store}/stock").json()[0]["quantity"] == "0"
        assert client.post(path, headers=headers, json=payload).status_code == 422
        assert (
            client.post(path, headers=posting, json={**payload, "confirmed": False}).status_code
            == 422
        )
        assert (
            client.post(path, headers=posting, json={**payload, "quantity": "0"}).status_code == 422
        )
        assert (
            client.post(path, headers=posting, json={**payload, "quantity": "1.5"}).status_code
            == 422
        )
        assert (
            client.post(
                path, headers=posting, json={**payload, "batch_number": "unsupported"}
            ).status_code
            == 422
        )
        first = client.post(path, headers=posting, json=payload)
        assert first.status_code == 201, first.text
        assert client.post(path, headers=posting, json=payload).json()["id"] == first.json()["id"]
        assert (
            client.post(path, headers=posting, json={**payload, "quantity": "20"}).status_code
            == 409
        )
        assert (
            client.post(
                path, headers={**headers, "Idempotency-Key": str(uuid4())}, json=payload
            ).status_code
            == 409
        )
        stock = client.get(base + f"/stores/{store}/stock").json()[0]
        assert Decimal(stock["quantity"]) == Decimal("10.000")
        assert Decimal(stock["opening_value"]) == Decimal("185.00")
        assert len(client.get(base + f"/stores/{store}/stock-movements").json()) == 1
        assert (
            len(
                [
                    row
                    for row in client.get(base + "/audit").json()
                    if row["action"] == "stock.opening_recorded"
                ]
            )
            == 1
        )
        for statement in (
            "UPDATE stock_movement SET quantity = 999",
            "DELETE FROM stock_movement",
            "UPDATE stock_batch SET batch_number = 'tamper'",
        ):
            with pytest.raises(DBAPIError), postgres_case.admin.begin() as connection:
                connection.execute(text(statement))
        with postgres_case.admin.connect() as connection:
            assert connection.execute(select(func.count()).select_from(batches)).scalar() == 1


def test_batch_expiry_weighted_goods_and_missing_resources(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        item = create_product(
            client, base, headers, unit="kg", batch_tracking=True, expiry_tracking=True
        )
        store = store_id(client, base)
        path = base + f"/stores/{store}/opening-stock"
        payload = opening_payload(
            item["id"], quantity="1.125", batch_number="LOT-A", expiry_date="2027-01-31"
        )

        def post(value):
            return client.post(
                path, headers={**headers, "Idempotency-Key": str(uuid4())}, json=value
            )

        assert post({**payload, "expiry_date": None}).status_code == 422
        assert post({**payload, "product_id": str(uuid4())}).status_code == 404
        assert post(payload).status_code == 201
        assert post({**payload, "batch_number": "LOT-B", "quantity": "0.875"}).status_code == 201
        stock = client.get(base + f"/stores/{store}/stock").json()[0]
        assert Decimal(stock["quantity"]) == Decimal("2.000")
        assert Decimal(stock["opening_value"]) == Decimal("37.00000")
        for ending in ("stock", "stock-movements"):
            assert client.get(base + f"/stores/{uuid4()}/{ending}").status_code == 404
            assert (
                client.get(base + f"/stores/{store}/{ending}", params={"limit": 0}).status_code
                == 422
            )
        assert (
            client.post(
                base + f"/stores/{uuid4()}/opening-stock",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json=payload,
            ).status_code
            == 404
        )
        with postgres_case.admin.begin() as connection:
            connection.execute(
                products.update().where(products.c.id == item["id"]).values(active=False)
            )
        assert post({**payload, "batch_number": "LOT-C"}).status_code == 404


def test_staff_capabilities_hide_costs_and_enforce_store_and_tenant_scope(
    postgres_case: PostgreSQLCase,
):
    with (
        postgres_case.client() as owner,
        postgres_case.client() as cashier,
        postgres_case.client() as inventory,
    ):
        headers = register(owner)
        cashier_headers = register(cashier, "cashier@example.com")
        inventory_headers = register(inventory, "inventory@example.com")
        base = "/api/v1/businesses/" + business(owner, headers)
        item = create_product(owner, base, headers, barcodes=["8900"])
        store = store_id(owner, base)
        other_store = owner.post(base + "/stores", headers=headers, json={"name": "Other"}).json()[
            "id"
        ]
        for email, role in (
            ("cashier@example.com", "CASHIER"),
            ("inventory@example.com", "INVENTORY_MANAGER"),
        ):
            assert (
                owner.post(
                    base + "/members",
                    headers=headers,
                    json={"email": email, "role": role, "store_ids": [store]},
                ).status_code
                == 201
            )
        for result in (
            cashier.get(base + "/products"),
            cashier.get(base + "/products/resolve-barcode", params={"value": "8900"}),
        ):
            record = result.json()[0] if isinstance(result.json(), list) else result.json()
            assert record["purchase_price"] is None and record["landed_cost"] is None
        assert (
            cashier.post(
                base + "/products", headers=cashier_headers, json=product_payload()
            ).status_code
            == 403
        )
        assert cashier.get(base + "/suppliers").status_code == 403
        assert (
            cashier.get(base + "/products/matches", params={"name": "biscuit"}).status_code == 403
        )
        assert inventory.get(base + f"/stores/{store}/stock").status_code == 200
        assert inventory.get(base + f"/stores/{other_store}/stock").status_code == 404
        assert inventory.get(base + f"/stores/{other_store}/stock-movements").status_code == 404
        assert (
            inventory.post(
                base + f"/stores/{store}/opening-stock",
                headers={**inventory_headers, "Idempotency-Key": str(uuid4())},
                json=opening_payload(item["id"]),
            ).status_code
            == 403
        )
        other = "/api/v1/businesses/" + business(owner, headers, "Other tenant")
        for endpoint in ("products", "suppliers", "brands", "categories"):
            assert cashier.get(other + "/" + endpoint).status_code == 404
        assert (
            cashier.post(
                base + "/brands", headers=cashier_headers, json={"name": "Denied"}
            ).status_code
            == 403
        )
        assert (
            owner.post(
                base + "/suppliers",
                headers={"Origin": "http://testserver"},
                json={"name": "Denied"},
            ).status_code
            == 403
        )


def test_concurrent_stock_replays_post_once_and_independent_keys_reject_duplicate_lots(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        item = create_product(client, base, headers)
        path = base + f"/stores/{store_id(client, base)}/opening-stock"
        payload = opening_payload(item["id"])
        key = str(uuid4())

        def post(stock_key):
            return client.post(
                path, headers={**headers, "Idempotency-Key": stock_key}, json=payload
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(post, [key, key]))
        assert all(result.status_code == 201 for result in responses)
        assert responses[0].json()["id"] == responses[1].json()["id"]
        assert post(str(uuid4())).status_code == 409
        with postgres_case.admin.connect() as connection:
            assert connection.execute(select(func.count()).select_from(movements)).scalar() == 1


def test_product_validation_rejects_float_rounding_invalid_prices_and_duplicates(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        for overrides in (
            {"purchase_price": 18.5},
            {"purchase_price": "18.505"},
            {"purchase_price": "NaN"},
            {"selling_price": "31"},
            {"minimum_selling_price": "26"},
            {"expiry_tracking": True},
            {"barcodes": ["same", "same"]},
            {"barcodes": ["spaces not allowed"]},
            {"alternate_names": [" "]},
            {"name": "!!!"},
            {"reorder_quantity": "1.5"},
            {"hsn": "invalid"},
            {"hsn": "abc1234"},
            {"sku": "SKU with spaces"},
            {"gst_rate": "101"},
            {"purchase_price": "-1"},
            {"active": True},
        ):
            result = client.post(
                base + "/products", headers=headers, json=product_payload(**overrides)
            )
            assert result.status_code == 422, result.text
        assert client.get(base + "/products").json() == []


def test_failed_audit_rolls_back_stock_batch_and_movement(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers = register(client)
        base = "/api/v1/businesses/" + business(client, headers)
        item = create_product(client, base, headers)
        path = base + f"/stores/{store_id(client, base)}/opening-stock"
        posting = {**headers, "Idempotency-Key": str(uuid4())}
        with postgres_case.admin.begin() as connection:
            connection.execute(
                text("""
                CREATE FUNCTION simulate_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.action = 'stock.opening_recorded' THEN
                    RAISE EXCEPTION 'Isolated test audit failure';
                  END IF;
                  RETURN NEW;
                END; $$
            """)
            )
            connection.execute(
                text("""
                CREATE TRIGGER test_audit_failure BEFORE INSERT ON audit_log
                FOR EACH ROW EXECUTE FUNCTION simulate_audit_failure()
            """)
            )
        response = client.post(path, headers=posting, json=opening_payload(item["id"]))
        assert response.status_code == 503
        assert response.json() == {"detail": "Database operation unavailable"}
        with postgres_case.admin.begin() as connection:
            assert connection.execute(select(func.count()).select_from(batches)).scalar() == 0
            assert connection.execute(select(func.count()).select_from(movements)).scalar() == 0
            connection.execute(text("DROP TRIGGER test_audit_failure ON audit_log"))
            connection.execute(text("DROP FUNCTION simulate_audit_failure()"))
        assert (
            client.post(path, headers=posting, json=opening_payload(item["id"])).status_code == 201
        )
