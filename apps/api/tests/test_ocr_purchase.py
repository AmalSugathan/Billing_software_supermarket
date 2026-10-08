"""Synthetic source review exercises real PostgreSQL financial posting atomicity."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from test_ocr import upload
from test_purchases import payload, post, setup

from supermarket.identity import set_context
from supermarket.ocr_models import attempts, results
from supermarket.ocr_purchase_models import links

pytestmark = pytest.mark.database


def test_source_purchase_review_atomicity_replay_scope_and_no_payment(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client:
        headers, base, store, supplier, product = setup(client)
        store_path = base + "/stores/" + store
        path = store_path + "/purchases"
        actor = client.get("/api/v1/auth/session").json()["user"]["id"]
        document = upload(client, store_path, headers).json()
        attempt_id = str(uuid4())
        data = payload(
            supplier, product["id"], source_document_id=document["id"], source_attempt_id=attempt_id
        )
        assert post(client, path, headers, {**data, "source_attempt_id": None}).status_code == 422
        assert post(client, path, headers, data).status_code == 409
        assert Decimal(client.get(store_path + "/stock").json()[0]["quantity"]) == 0
        with postgres_case.admin.begin() as connection:
            connection.execute(
                insert(attempts).values(
                    id=attempt_id,
                    business_id=base.split("/")[-1],
                    store_id=store,
                    actor_user_id=actor,
                    document_id=document["id"],
                    provider_model="PaddleOCR-VL-1.6",
                    reason="Synthetic review fixture",
                    idempotency_key=str(uuid4()),
                    request_hash="a" * 64,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=240),
                )
            )
            connection.execute(
                insert(results).values(
                    id=str(uuid4()),
                    business_id=base.split("/")[-1],
                    store_id=store,
                    actor_user_id=actor,
                    attempt_id=attempt_id,
                    status="review_required",
                    evidence_ciphertext=b"synthetic evidence; no inference claim",
                )
            )
        assert post(client, path, headers, {**data, "confirmed": False}).status_code == 422
        assert client.post(path + "/preview", headers=headers, json=data).status_code == 200
        assert (
            post(client, path, headers, {**data, "source_document_id": str(uuid4())}).status_code
            == 404
        )
        assert post(client, path, headers, {**data, "invoice_total": "503"}).status_code == 422
        with postgres_case.admin.connect() as connection:
            assert connection.execute(select(links)).mappings().all() == []
        key = str(uuid4())
        posted = post(client, path, headers, data, key)
        assert posted.status_code == 201, posted.text
        assert posted.json()["source_document_id"] == document["id"]
        assert posted.json()["source_sha256"] == document["sha256"]
        assert post(client, path, headers, data, key).json() == posted.json()
        assert (
            post(client, path, headers, {**data, "invoice_number": "DEMO-OTHER"}).status_code == 409
        )
        assert Decimal(client.get(store_path + "/stock").json()[0]["quantity"]) == 48
        assert client.get(store_path + "/money-movements").json() == []
        with postgres_case.runtime.begin() as connection:
            set_context(connection, actor, base.split("/")[-1])
            assert len(connection.execute(select(links)).all()) == 1
        with pytest.raises(DBAPIError), postgres_case.runtime.begin() as connection:
            set_context(connection, actor, base.split("/")[-1])
            connection.execute(text("DELETE FROM ocr_purchase_link"))
        with postgres_case.runtime.begin() as connection:
            set_context(connection, actor, str(uuid4()))
            assert connection.execute(select(links)).all() == []
