"""Offline quotas isolate stock and keep received cash atomic across sync retries."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from test_commerce import checkout, prepared
from test_finance import post

pytestmark = pytest.mark.database


def test_offline_reservation_sync_replay_and_drawer_closing(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        data = {
            "terminal_id": terminal,
            "cash_session_id": session,
            "products": [{"product_id": product["id"], "quantity": "8"}],
            "reason": "Synthetic offline quota",
            "confirmed": True,
        }
        key = str(uuid4())
        prepared_lease = post(client, path + "/offline-leases", headers, data, key)
        assert prepared_lease.status_code == 201, prepared_lease.text
        lease = prepared_lease.json()
        assert post(client, path + "/offline-leases", headers, data, key).json() == lease
        assert post(client, path + "/offline-leases", headers, data).status_code == 409
        assert len(client.get(path + "/offline-leases").json()) == 1
        assert (
            post(
                client,
                path + "/sales",
                headers,
                checkout(
                    product,
                    terminal,
                    cash["id"],
                    session,
                    lines=[{"product_id": product["id"], "quantity": "3", "expected_price": "25"}],
                    payments=[
                        {
                            "account_id": cash["id"],
                            "cash_session_id": session,
                            "method": "cash",
                            "amount": "75",
                        }
                    ],
                ),
            ).status_code
            == 409
        )
        assert (
            post(
                client,
                path + "/cash-sessions/" + session + "/close",
                headers,
                {
                    "expected_cash": "1000",
                    "actual_cash": "1000",
                    "reason": "Synthetic closing",
                    "confirmed": True,
                },
            ).status_code
            == 409
        )
        sale_id = str(uuid4())
        synced = {
            "client_sale_id": sale_id,
            "sequence": 1,
            "completed_at": datetime.now(UTC).isoformat(),
            "expected_total": "75",
            "received_cash": "100",
            "lines": [{"product_id": product["id"], "quantity": "3", "expected_price": "25"}],
            "reason": "Synthetic saved cash receipt",
            "confirmed": True,
        }
        route = path + "/offline-leases/" + lease["id"] + "/sales"
        sale = post(client, route, headers, synced, sale_id)
        assert sale.status_code == 201, sale.text
        assert sale.json()["id"] == sale_id
        assert post(client, route, headers, synced, sale_id).json() == sale.json()
        assert (
            post(client, route, headers, {**synced, "received_cash": "101"}, sale_id).status_code
            == 409
        )
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 7
        assert Decimal(client.get(path + "/cash-sessions").json()[0]["expected_cash"]) == 1075
        finalize = path + "/offline-leases/" + lease["id"] + "/finalize"
        assert (
            post(
                client,
                finalize,
                headers,
                {"last_sequence": 2, "reason": "Synthetic finalized journal", "confirmed": True},
            ).status_code
            == 409
        )
        sealed = post(
            client,
            finalize,
            headers,
            {"last_sequence": 1, "reason": "Synthetic finalized journal", "confirmed": True},
        )
        assert sealed.status_code == 200, sealed.text
        assert sealed.json()["sealed"] is True
        assert (
            post(
                client,
                path + "/cash-sessions/" + session + "/close",
                headers,
                {
                    "expected_cash": "1075",
                    "actual_cash": "1075",
                    "reason": "Synthetic closing after sync",
                    "confirmed": True,
                },
            ).status_code
            == 201
        )
        assert post(client, route, headers, synced, sale_id).status_code == 201


def test_offline_conflicts_preserve_server_state_and_require_owner_recovery(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, path, terminal, product, cash, session = prepared(client)
        lease = post(
            client,
            path + "/offline-leases",
            headers,
            {
                "terminal_id": terminal,
                "cash_session_id": session,
                "products": [{"product_id": product["id"], "quantity": "4"}],
                "reason": "Synthetic offline quota",
                "confirmed": True,
            },
        ).json()
        route = path + "/offline-leases/" + lease["id"] + "/sales"
        sale_id = str(uuid4())
        data = {
            "client_sale_id": sale_id,
            "sequence": 1,
            "completed_at": datetime.now(UTC).isoformat(),
            "expected_total": "125",
            "received_cash": "125",
            "lines": [{"product_id": product["id"], "quantity": "5", "expected_price": "25"}],
            "reason": "Synthetic saved cash receipt",
            "confirmed": True,
        }
        assert post(client, route, headers, data, sale_id).status_code == 409
        assert client.get(path + "/sales").json() == []
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 10
        bad = {
            **data,
            "sequence": 2,
            "lines": [{"product_id": product["id"], "quantity": "1", "expected_price": "25"}],
            "expected_total": "25",
        }
        assert post(client, route, headers, bad, sale_id).status_code == 409
        bad = {**bad, "sequence": 1, "completed_at": "2020-01-01T00:00:00Z"}
        assert post(client, route, headers, bad, sale_id).status_code == 409
        recovered = post(
            client,
            route,
            headers,
            {**bad, "recovery_reason": "Synthetic device clock error reviewed by owner"},
            sale_id,
        )
        assert recovered.status_code == 201, recovered.text


def test_offline_exact_replay_concurrency_permissions_and_sealed_new_receipt(
    postgres_case: PostgreSQLCase,
):
    from concurrent.futures import ThreadPoolExecutor

    from test_identity import register

    with postgres_case.client() as client, postgres_case.client() as outside:
        headers, base, path, terminal, product, cash, session = prepared(client)
        stranger = register(outside, "outside@example.com")
        prepare = {
            "terminal_id": terminal,
            "cash_session_id": session,
            "products": [{"product_id": product["id"], "quantity": "4"}],
            "reason": "Synthetic offline quota",
            "confirmed": True,
        }
        assert post(outside, path + "/offline-leases", stranger, prepare).status_code == 404
        assert outside.get(path + "/offline-leases").status_code == 404
        assert (
            post(
                client,
                path + "/offline-leases",
                headers,
                {**prepare, "products": [{"product_id": product["id"], "quantity": "11"}]},
            ).status_code
            == 409
        )
        lease = post(client, path + "/offline-leases", headers, prepare).json()
        sale_id = str(uuid4())
        route = path + "/offline-leases/" + lease["id"] + "/sales"
        data = {
            "client_sale_id": sale_id,
            "sequence": 1,
            "completed_at": datetime.now(UTC).isoformat(),
            "expected_total": "50",
            "received_cash": "100",
            "lines": [{"product_id": product["id"], "quantity": "2", "expected_price": "25"}],
            "reason": "Synthetic saved offline receipt",
            "confirmed": True,
        }
        assert post(client, route, headers, data).status_code == 422
        assert (
            post(client, route, headers, {**data, "expected_total": "49"}, sale_id).status_code
            == 409
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: post(client, route, headers, data, sale_id), range(2))
            )
        assert [item.status_code for item in results] == [201, 201]
        assert results[0].json() == results[1].json()
        current = client.get(path + "/offline-leases").json()[0]
        assert current["synced_sequence"] == 1
        assert Decimal(current["products"][0]["quantity"]) == 2
        seal = {"last_sequence": 1, "reason": "Synthetic finalized journal", "confirmed": True}
        key = str(uuid4())
        sealed = post(
            client, path + "/offline-leases/" + lease["id"] + "/finalize", headers, seal, key
        )
        assert sealed.status_code == 200
        assert (
            post(
                client, path + "/offline-leases/" + lease["id"] + "/finalize", headers, seal, key
            ).json()
            == sealed.json()
        )
        new_id = str(uuid4())
        assert (
            post(
                client, route, headers, {**data, "client_sale_id": new_id, "sequence": 2}, new_id
            ).status_code
            == 409
        )
        assert len(client.get(path + "/sales").json()) == 1
        assert Decimal(client.get(path + "/stock").json()[0]["quantity"]) == 8
