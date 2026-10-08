"""Real PostgreSQL/private intake and synthetic Paddle HTTP-contract tests."""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from conftest import PostgreSQLCase
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from test_catalog import create_product
from test_document_security import image_bytes
from test_finance import post, setup
from test_identity import register

from supermarket.document_security import EvidenceVault
from supermarket.identity import set_context
from supermarket.main import create_app
from supermarket.ocr_models import attempts, documents, results
from supermarket.ocr_provider import PaddleLayoutProvider, ProviderFailure

pytestmark = pytest.mark.database


def upload(client, path, headers, content=None, filename="DEMO-invoice.jpg", key=None):
    return client.post(
        path + "/ocr-documents?filename=" + filename + "&data_origin=synthetic",
        headers={**headers, "Content-Type": "image/jpeg", "Idempotency-Key": key or str(uuid4())},
        content=content or image_bytes(),
    )


def test_private_upload_preview_duplicate_scopes_and_immutable_evidence(
    postgres_case: PostgreSQLCase,
):
    with postgres_case.client() as client, postgres_case.client() as outside:
        headers, base, path, terminal = setup(client)
        other = register(outside, "outside@example.com")
        route = path + "/ocr-documents"
        source, key = image_bytes(), str(uuid4())
        uploaded = upload(client, path, headers, source, key=key)
        assert uploaded.status_code == 201, uploaded.text
        document = uploaded.json()
        assert document["status"] == "uploaded" and document["evidence"] is None
        assert upload(client, path, headers, source, key=key).json() == document
        assert upload(client, path, headers, source).status_code == 409
        assert (
            upload(client, path, headers, source, filename="changed.jpg", key=key).status_code
            == 409
        )
        assert upload(client, path, headers, b"not a jpeg").status_code == 422
        assert upload(client, path, headers, filename="../bad.jpg").status_code == 422
        listing = client.get(route)
        assert listing.json() == [document]
        assert listing.headers["cache-control"] == "no-store"
        assert outside.get(route).status_code == 404
        assert outside.get(route + "/" + document["id"] + "/content").status_code == 404
        assert upload(outside, path, other).status_code == 404
        assert client.get(route + "/provider").json()["provider_configured"] is False
        assert client.get(route + "/" + document["id"] + "/draft").status_code == 409
        assert (
            post(
                client,
                route + "/" + document["id"] + "/process",
                headers,
                {"reason": "Synthetic OCR request"},
            ).status_code
            == 503
        )
        downloaded = client.get(route + "/" + document["id"] + "/content?original=true")
        assert downloaded.content == source and downloaded.headers["cache-control"] == "no-store"
        assert downloaded.headers["content-disposition"].startswith("attachment")
        assert (
            client.get(route + "/" + document["id"] + "/content").headers["content-type"]
            == "image/jpeg"
        )
        with postgres_case.admin.begin() as connection:
            record = connection.execute(select(documents)).mappings().one()
            assert (
                record["original_ciphertext"] != source
                and source not in record["original_ciphertext"]
            )
            vault = EvidenceVault(postgres_case.settings.ocr_encryption_key)
            assert (
                vault.decrypt(
                    record["original_ciphertext"],
                    f"{record['business_id']}|{record['store_id']}|{record['id']}|original",
                )
                == source
            )
        with pytest.raises(DBAPIError), postgres_case.runtime.begin() as connection:
            set_context(
                connection,
                document.get(
                    "actor_user_id", client.get("/api/v1/auth/session").json()["user"]["id"]
                ),
                base.split("/")[-1],
            )
            connection.execute(
                text("DELETE FROM ocr_document WHERE id=:id"), {"id": document["id"]}
            )
        assert (
            client.get(path + "/stock").json() == []
            and client.get(path + "/purchases").json() == []
        )


def test_real_paddle_contract_adapter_and_failed_processing_keep_evidence(
    postgres_case: PostgreSQLCase,
):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        failed = False

        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            if self.failed:
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "errorCode": 0,
                        "result": {
                            "layoutParsingResults": [
                                {
                                    "prunedResult": {
                                        "parsing_res_list": [
                                            {
                                                "block_label": "text",
                                                "block_content": (
                                                    "DEMO synthetic invoice total 50.00"
                                                ),
                                                "block_bbox": [0, 0, 100, 100],
                                            }
                                        ]
                                    },
                                    "markdown": {"text": "DEMO synthetic invoice total 50.00"},
                                }
                            ]
                        },
                    }
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = replace(
            postgres_case.settings, ocr_service_url=f"http://127.0.0.1:{server.server_port}"
        )
        with TestClient(create_app(settings, postgres_case.runtime)) as client:
            headers, base, path, terminal = setup(client)
            document = upload(client, path, headers).json()
            route = path + "/ocr-documents/" + document["id"] + "/process"
            key, body = str(uuid4()), {"reason": "Synthetic extraction contract test"}
            processed = post(client, route, headers, body, key)
            assert processed.status_code == 202, processed.text
            assert processed.json()["status"] == "processing"
            processed = client.get(route.removesuffix("/process"))
            assert processed.json()["status"] == "review_required"
            draft = client.get(route.removesuffix("/process") + "/draft")
            assert draft.status_code == 200, draft.text
            assert draft.json()["posting_allowed"] is False
            assert draft.json()["source_sha256"] == document["sha256"]
            assert processed.json()["evidence"]["pages"][0]["blocks"][0]["confidence"] is None
            assert post(client, route, headers, body, key).json() == processed.json()
            assert (
                len(calls) == 1
                and calls[0]["fileType"] == 1
                and calls[0]["returnMarkdownImages"] is False
            )
            assert base64.b64decode(calls[0]["file"]).startswith(b"\xff\xd8")
            assert (
                post(client, route, headers, {"reason": "Changed request"}, key).status_code == 409
            )
            Handler.failed = True
            failed = post(client, route, headers, body)
            assert failed.status_code == 202
            failed = client.get(route.removesuffix("/process"))
            assert (
                failed.json()["status"] == "failed"
                and failed.json()["error_code"] == "service_unavailable"
            )
            assert (
                client.get(
                    path + "/ocr-documents/" + document["id"] + "/content?original=true"
                ).status_code
                == 200
            )
            events = client.get(base + "/audit").json()
            assert any(
                event["source"] == "ai" and event["action"] == "ocr.processing.completed"
                for event in events
            )
            assert client.get(path + "/purchases").json() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    for endpoint in (
        "https://external.example",
        "file:///private",
        "http://localhost/path",
        "http://user:password@localhost",
    ):
        with pytest.raises(ValueError):
            PaddleLayoutProvider(endpoint)
    with pytest.raises(ProviderFailure):
        PaddleLayoutProvider(f"http://127.0.0.1:{server.server_port}").infer(
            image_bytes(), "image/jpeg"
        )


def test_matching_pack_mismatch_and_reviewed_supplier_alias(postgres_case: PostgreSQLCase):
    with postgres_case.client() as client:
        headers, base, path, terminal = setup(client)
        product = create_product(client, base, headers)
        create_product(
            client,
            base,
            headers,
            sku="GOOD-DAY-200",
            name="Good Day Cashew Biscuit 200g",
            confirm_distinct_product=True,
        )
        supplier = client.post(
            base + "/suppliers", headers=headers, json={"name": "DEMO supplier"}
        ).json()
        document = upload(client, path, headers).json()
        route = path + "/ocr-documents/" + document["id"]
        response = client.get(route + "/matches", params={"q": "Britannia Good Day Cashew 100G"})
        assert response.status_code == 200, response.text
        matches = response.json()
        assert product["id"] in [match["product_id"] for match in matches]
        assert all("200g" not in match["name"] for match in matches)
        body = {
            "supplier_id": supplier["id"],
            "product_id": product["id"],
            "supplier_description": "BRIT GDC CASHEW 100G",
            "reason": "Synthetic label verified by owner",
            "confirmed": True,
        }
        key = str(uuid4())
        mapped = post(client, route + "/mappings", headers, body, key)
        assert mapped.status_code == 200, mapped.text
        assert post(client, route + "/mappings", headers, body, key).json() == mapped.json()
        assert post(client, route + "/mappings", headers, body).status_code == 409
        matches = client.get(
            route + "/matches",
            params={"q": body["supplier_description"], "supplier_id": supplier["id"]},
        ).json()
        assert (
            matches[0]["method"] == "reviewed_supplier_mapping"
            and matches[0]["product_id"] == product["id"]
        )
        assert matches[0]["confidence"] is None and matches[0]["requires_review"] is True


def test_crashed_ocr_worker_retains_attempt_and_requires_explicit_retry(
    postgres_case: PostgreSQLCase,
):
    configuration = replace(postgres_case.settings, ocr_service_url="http://127.0.0.1:1")
    with TestClient(create_app(configuration, postgres_case.runtime)) as client:
        headers, base, path, terminal = setup(client)
        actor = client.get("/api/v1/auth/session").json()["user"]["id"]
        route = path + "/ocr-documents"
        for expired in (False, True):
            document = upload(
                client, path, headers, image_bytes(color="red" if expired else "blue")
            ).json()
            attempt_id = str(uuid4())
            with postgres_case.admin.begin() as connection:
                connection.execute(
                    insert(attempts).values(
                        id=attempt_id,
                        business_id=base.split("/")[-1],
                        store_id=document["store_id"],
                        actor_user_id=actor,
                        document_id=document["id"],
                        provider_model="PaddleOCR-VL-1.6",
                        reason="Synthetic interrupted worker",
                        idempotency_key=str(uuid4()),
                        request_hash="a" * 64,
                        lease_expires_at=datetime.now(UTC)
                        + timedelta(seconds=-1 if expired else 240),
                    )
                )
            detail = client.get(route + "/" + document["id"]).json()
            assert detail["status"] == ("retry_due" if expired else "processing")
            response = post(
                client,
                route + "/" + document["id"] + "/process",
                headers,
                {"reason": "Explicit retry after review"},
            )
            if not expired:
                assert response.status_code == 409
            else:
                assert response.status_code == 202, response.text
                response = client.get(route + "/" + document["id"])
                assert response.json()["status"] == "failed"
                with postgres_case.admin.begin() as connection:
                    preserved = (
                        connection.execute(
                            select(results).where(results.c.attempt_id == attempt_id)
                        )
                        .mappings()
                        .one()
                    )
                    assert preserved["error_code"] == "worker_interrupted"
                audit = client.get(base + "/audit").json()
                assert any(
                    item["action"] == "ocr.processing.interrupted" and item["source"] == "ai"
                    for item in audit
                )
        assert client.get(path + "/stock").json() == []
        assert client.get(path + "/purchases").json() == []
