"""Synthetic fixtures test real PostgreSQL posting, permissions and recovery."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import PostgreSQLCase
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_catalog import create_product, opening_payload, store_id
from test_identity import business, register

from supermarket.catalog_models import batches, movements
from supermarket.identity import set_context
from supermarket.purchase_models import items, purchases

pytestmark = pytest.mark.database


def setup(client):
    headers = register(client)
    base = "/api/v1/businesses/" + business(client, headers)
    supplier = client.post(
        base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
    ).json()["id"]
    product = create_product(client, base, headers)
    store = store_id(client, base)
    return headers, base, store, supplier, product


def line(item_id, **changes):
    return {
        "product_id": item_id,
        "supplier_description": "DEMO supplier biscuit case",
        "purchase_unit": "carton",
        "purchase_quantity": "2",
        "units_per_purchase": "24",
        "free_stock_quantity": "0",
        "conversion_evidence": "Synthetic bill: 24 pieces per carton",
        "unit_rate": "240.00",
        "discount": "0",
        "gst_rate": "5",
        "hsn": "1905",
        **changes,
    }


def payload(supplier, product_id, **changes):
    return {
        "supplier_id": supplier,
        "invoice_number": "DEMO-INV-001",
        "invoice_date": "2026-10-08",
        "tax_mode": "exclusive",
        "tax_kind": "intra",
        "round_off": "0",
        "invoice_total": "504.00",
        "review_reason": "Reviewed synthetic invoice and pack quantities",
        "lines": [line(product_id)],
        "confirmed": True,
        **changes,
    }


def post(client, path, headers, data, key=None):
    return client.post(path, headers={**headers, "Idempotency-Key": key or str(uuid4())}, json=data)


def test_purchase_preview_post_replay_snapshot_and_opening_lot_reuse(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, store, supplier, product = setup(client)
        path = base + f"/stores/{store}/purchases"
        opening = client.post(
            base + f"/stores/{store}/opening-stock",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json=opening_payload(product["id"]),
        )
        assert opening.status_code == 201
        data = payload(supplier, product["id"])
        review = client.post(path + "/preview", headers=headers, json={**data, "confirmed": False})
        assert review.status_code == 200, review.text
        assert Decimal(review.json()["lines"][0]["stock_quantity"]) == 48
        assert Decimal(review.json()["cgst"]) == Decimal("12.00")
        assert client.get(path).json() == []
        key = str(uuid4())
        first = post(client, path, headers, data, key)
        assert first.status_code == 201, first.text
        stored = first.json()
        assert post(client, path, headers, data, key).json() == stored
        assert Decimal(client.get(base + f"/stores/{store}/stock").json()[0]["quantity"]) == 58
        assert stored["lines"][0]["batch_id"] == opening.json()["batch_id"]
        assert stored["lines"][0]["conversion_evidence"] == data["lines"][0]["conversion_evidence"]
        assert stored["lines"][0]["purchase_unit"] == "carton"
        assert client.get(path + "/" + stored["id"]).json() == stored
        assert len(client.get(path, params={"limit": 1}).json()) == 1
        assert client.get(base + "/products").json()[0]["purchase_price"] == "18.50"
        assert (
            len(
                [
                    row
                    for row in client.get(base + "/audit").json()
                    if row["action"] == "purchase.posted"
                ]
            )
            == 1
        )
        assert (
            post(client, path, headers, {**data, "review_reason": "Changed data"}, key).status_code
            == 409
        )
        assert post(client, path, headers, data).status_code == 409
        assert (
            post(client, path, headers, {**data, "invoice_number": " demo-inv-001 "}).status_code
            == 409
        )
        for table in ("purchase", "purchase_item"):
            with pytest.raises(DBAPIError), postgres_case.admin.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
        with postgres_case.runtime.begin() as connection:
            assert connection.execute(select(purchases)).all() == []
            actor = client.get("/api/v1/auth/session").json()["user"]["id"]
            set_context(connection, actor, base.rsplit("/", 1)[1])
            assert len(connection.execute(select(items)).all()) == 1


def test_repeated_products_concurrent_retry_and_financial_year_duplicate_scope(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, store, supplier, product = setup(client)
        path = base + f"/stores/{store}/purchases"
        data = payload(
            supplier,
            product["id"],
            lines=[line(product["id"]), line(product["id"])],
            invoice_total="1008.00",
        )
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: post(client, path, headers, data, key), range(2)))
        assert all(response.status_code == 201 for response in responses), [
            r.text for r in responses
        ]
        assert responses[0].json()["id"] == responses[1].json()["id"]
        assert len(responses[0].json()["lines"]) == 2
        assert Decimal(client.get(base + f"/stores/{store}/stock").json()[0]["quantity"]) == 96
        assert (
            post(client, path, headers, {**data, "invoice_date": "2027-03-31"}).status_code == 409
        )
        assert (
            post(client, path, headers, {**data, "invoice_date": "2027-04-01"}).status_code == 201
        )
        assert client.get(path, params={"limit": 1, "offset": 1}).status_code == 200
        assert client.get(path, params={"limit": 201}).status_code == 422
        with postgres_case.admin.connect() as connection:
            assert connection.execute(select(func.count()).select_from(batches)).scalar() == 1
            assert connection.execute(select(func.count()).select_from(movements)).scalar() == 4


def test_purchase_validation_and_batch_precision(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, store, supplier, product = setup(client)
        path = base + f"/stores/{store}/purchases"
        data = payload(supplier, product["id"])
        for change in (
            {"confirmed": False},
            {"invoice_total": "503.99"},
            {"round_off": "2"},
            {"round_off": 0.0},
            {"supplier_id": str(uuid4())},
        ):
            assert post(client, path, headers, {**data, **change}).status_code in {404, 422}
        for change in (
            {"unit_rate": 240.0},
            {"unit_rate": "NaN"},
            {"purchase_quantity": "0"},
            {"purchase_quantity": "1.5"},
            {"units_per_purchase": "0"},
            {"units_per_purchase": "0.1"},
            {"purchase_unit": "pcs"},
            {"discount": "481"},
            {"batch_number": "not-tracked"},
            {"product_id": str(uuid4())},
        ):
            result = post(client, path, headers, {**data, "lines": [line(product["id"], **change)]})
            assert result.status_code in {404, 422}, result.text
        assert post(client, path, {"Origin": "http://testserver"}, data).status_code == 403
        assert client.post(path, headers=headers, json=data).status_code == 422
        assert client.get(path + "/" + str(uuid4())).status_code == 404
        assert client.get(path).json() == []
        tracked = create_product(
            client,
            base,
            headers,
            sku="TRACKED",
            name="Completely different tracked item",
            unit="kg",
            batch_tracking=True,
            expiry_tracking=True,
        )
        weighted = payload(
            supplier,
            tracked["id"],
            invoice_number="WEIGHTED",
            invoice_total="1.02",
            lines=[
                line(
                    tracked["id"],
                    purchase_unit="kg",
                    purchase_quantity="0.125",
                    units_per_purchase="1",
                    unit_rate="8.123456",
                    gst_rate="0",
                    batch_number="LOT",
                    expiry_date="2027-01-31",
                )
            ],
        )
        assert (
            post(
                client,
                path,
                headers,
                {**weighted, "lines": [{**weighted["lines"][0], "expiry_date": None}]},
            ).status_code
            == 422
        )
        recorded = post(client, path, headers, weighted)
        assert recorded.status_code == 201, recorded.text
        assert Decimal(recorded.json()["lines"][0]["unit_cost"]) == Decimal("8.160000")
        assert recorded.json()["lines"][0]["batch_number"] == "LOT"


def test_purchase_store_role_and_tenant_isolation(postgres_case: PostgreSQLCase):
    with (
        postgres_case.client() as owner,
        postgres_case.client() as cashier,
        postgres_case.client() as buyer,
    ):
        headers, base, store, supplier, product = setup(owner)
        cash_headers = register(cashier, "cash@example.com")
        buyer_headers = register(buyer, "buyer@example.com")
        for email, role in (
            ("cash@example.com", "CASHIER"),
            ("buyer@example.com", "PURCHASE_MANAGER"),
        ):
            assert (
                owner.post(
                    base + "/members",
                    headers=headers,
                    json={"email": email, "role": role, "store_ids": [store]},
                ).status_code
                == 201
            )
        other_store = owner.post(
            base + "/stores", headers=headers, json={"name": "Other store"}
        ).json()["id"]
        path = base + f"/stores/{store}/purchases"
        data = payload(supplier, product["id"])
        assert cashier.get(path).status_code == 403
        assert post(cashier, path, cash_headers, data).status_code == 403
        assert post(buyer, path, buyer_headers, data).status_code == 201
        denied = base + f"/stores/{other_store}/purchases"
        assert buyer.get(denied).status_code == 404
        assert post(buyer, denied, buyer_headers, data).status_code == 404
        assert buyer.post(denied + "/preview", headers=buyer_headers, json=data).status_code == 404
        other = "/api/v1/businesses/" + business(owner, headers, "Other tenant")
        assert buyer.get(other + f"/stores/{store}/purchases").status_code == 404
        foreign = create_product(owner, other, headers)
        assert (
            post(
                owner,
                path,
                headers,
                {**data, "invoice_number": "FOREIGN", "lines": [line(foreign["id"])]},
            ).status_code
            == 404
        )
        first = owner.get(path).json()[0]
        assert owner.get(denied + "/" + first["id"]).status_code == 404
        assert (
            post(owner, denied, headers, {**data, "invoice_number": "ANOTHER"}).status_code == 201
        )


def test_purchase_audit_failure_rolls_back_all_posting_and_retry(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, store, supplier, product = setup(client)
        path = base + f"/stores/{store}/purchases"
        data = payload(supplier, product["id"])
        key = str(uuid4())
        with postgres_case.admin.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION fail_purchase_audit() RETURNS trigger LANGUAGE "
                    "plpgsql AS $$ BEGIN IF NEW.action = 'purchase.posted' THEN RAISE "
                    "EXCEPTION 'Synthetic audit failure'; END IF; RETURN NEW; END; $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER test_purchase_failure BEFORE INSERT ON audit_log FOR "
                    "EACH ROW EXECUTE FUNCTION fail_purchase_audit()"
                )
            )
        assert post(client, path, headers, data, key).status_code == 503
        with postgres_case.admin.begin() as connection:
            for table in (purchases, items, batches, movements):
                assert connection.execute(select(func.count()).select_from(table)).scalar() == 0
            connection.execute(text("DROP TRIGGER test_purchase_failure ON audit_log"))
            connection.execute(text("DROP FUNCTION fail_purchase_audit()"))
        assert post(client, path, headers, data, key).status_code == 201


def test_precision_losing_downgrade_is_rejected_without_losing_purchase(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, store, supplier, product = setup(client)
        path = base + f"/stores/{store}/purchases"
        data = payload(
            supplier,
            product["id"],
            invoice_total="1.05",
            lines=[line(product["id"], purchase_quantity="1", unit_rate="1.00")],
        )
        result = post(client, path, headers, data)
        assert result.status_code == 201, result.text
        configuration = Config("alembic.ini")
        configuration.attributes["database_url"] = postgres_case.admin_url
        with pytest.raises(DBAPIError):
            command.downgrade(configuration, "0003_catalog_stock")
        assert client.get(path).json()[0]["id"] == result.json()["id"]
