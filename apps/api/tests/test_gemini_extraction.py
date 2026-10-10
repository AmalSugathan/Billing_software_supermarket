"""Synthetic Gemini responses test validation; no test claims real model inference."""

import json
from decimal import Decimal
from urllib.error import HTTPError, URLError

import pytest
from pydantic import ValidationError

from supermarket.config import Settings
from supermarket.gemini_provider import GeminiFlashProvider
from supermarket.invoice_draft import propose
from supermarket.invoice_extraction import ExtractedInvoice, ExtractedItem
from supermarket.ocr_provider import MAX_OUTPUT, ProviderFailure


def rice(**changes):
    return {
        "original_name": "Rice Kozhi (Rooster) 50Kg",
        "normalized_name": "Rice Kozhi Rooster",
        "invoice_quantity": "2",
        "invoice_unit": "KG",
        "detected_pack_size": "50",
        "pack_unit": "KG",
        "purchase_unit_interpretation": "BAG",
        "stock_unit": "KG",
        "stock_quantity": "100",
        "unit_rate": "2275",
        "cost_per_kg": "45.50",
        "requires_review": False,
        "source_text": "Rice Kozhi 50Kg | 2 KG | 2275 | 4550",
        **changes,
    }


def invoice(**changes):
    return {
        "page_count": 1,
        "supplier_name": "SYNTHETIC supplier",
        "supplier_gstin": None,
        "invoice_number": "SYNTHETIC-1",
        "invoice_date": "2026-10-09",
        "invoice_total": "4550",
        "items": [rice()],
        **changes,
    }


def response(payload=None, finish="STOP"):
    return json.dumps(
        {
            "candidates": [
                {
                    "finishReason": finish,
                    "content": {"parts": [{"text": json.dumps(payload or invoice())}]},
                }
            ]
        }
    ).encode()


def test_owner_example_exact_math_and_unit_conflict_review():
    item = ExtractedItem.model_validate(rice())
    assert item.stock_quantity == Decimal("100")
    assert item.cost_per_kg == Decimal("45.50")
    assert item.requires_review is True
    assert "Printed invoice unit" in item.review_reasons[0]
    clean = ExtractedItem.model_validate(rice(invoice_unit="BAG"))
    assert clean.requires_review is False
    assert ExtractedItem.model_validate_json(clean.model_dump_json()) == clean


@pytest.mark.parametrize(
    "changes",
    [
        {"stock_quantity": "2"},
        {"cost_per_kg": "2275"},
        {"invoice_quantity": "-2"},
        {"invoice_quantity": True},
        {"unit_rate": 45.5},
        {"unit_rate": "NaN"},
        {"unit_rate": "Infinity"},
        {"requires_review": "false"},
        {"unknown": 2},
        {"invoice_quantity": "0.5", "stock_quantity": "25"},
        {"stock_unit": "L"},
        {"purchase_unit_interpretation": "BOX"},
        {"invoice_quantity": None},
        {"stock_quantity": "9999999999999"},
        {"gst_rate": "101"},
        {"hsn": "123"},
        {"expiry": "2026-02-31"},
    ],
)
def test_invalid_quantities_units_costs_and_injection_fields_rejected(changes):
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate(rice(**changes))


def test_cartons_pieces_loose_mass_volume_and_retail_pack_content():
    carton = ExtractedItem.model_validate(
        rice(
            invoice_unit="CARTON",
            purchase_unit_interpretation="CARTON",
            detected_pack_size="24",
            pack_unit="PCS",
            stock_unit="PCS",
            stock_quantity="48",
            cost_per_kg=None,
        )
    )
    assert carton.conversion_factor() == 24
    # Weight in a retail name must not multiply a piece count.
    retail = ExtractedItem.model_validate(
        rice(
            invoice_unit="PCS",
            purchase_unit_interpretation="PCS",
            detected_pack_size="500",
            pack_unit="G",
            stock_unit="PCS",
            stock_quantity="2",
            cost_per_kg=None,
        )
    )
    assert retail.stock_quantity == 2 and retail.conversion_factor() == 1
    loose = ExtractedItem.model_validate(
        rice(purchase_unit_interpretation="KG", stock_quantity="2", cost_per_kg="2275")
    )
    assert loose.stock_quantity == 2
    small = ExtractedItem.model_validate(
        rice(
            invoice_unit="PACK",
            purchase_unit_interpretation="PACK",
            detected_pack_size="500",
            pack_unit="G",
            stock_quantity="1",
            unit_rate="20",
            cost_per_kg="40",
        )
    )
    assert small.conversion_factor() == Decimal("0.500")
    volume = ExtractedItem.model_validate(
        rice(
            invoice_unit="BAG",
            detected_pack_size="500",
            pack_unit="ML",
            stock_unit="L",
            stock_quantity="1",
            cost_per_kg=None,
        )
    )
    assert volume.conversion_factor() == Decimal("0.5")
    grams = ExtractedItem.model_validate(
        rice(
            invoice_unit="G",
            purchase_unit_interpretation="G",
            stock_quantity="0.002",
            cost_per_kg="2275000",
        )
    )
    assert grams.stock_quantity == Decimal("0.002")


def test_unknown_pack_is_reviewable_but_cannot_invent_stock():
    item = ExtractedItem.model_validate(
        rice(detected_pack_size=None, stock_quantity=None, cost_per_kg=None)
    )
    assert item.requires_review and item.stock_quantity is None
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate(rice(detected_pack_size=None, cost_per_kg=None))
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate(
            rice(detected_pack_size="0.000001", stock_quantity=None, cost_per_kg=None)
        )
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate(
            rice(
                invoice_unit="CARTON",
                purchase_unit_interpretation="CARTON",
                detected_pack_size="1.25",
                pack_unit="PCS",
                stock_unit="PCS",
                stock_quantity="2.5",
                cost_per_kg=None,
            )
        )
    missing = ExtractedItem.model_validate(
        rice(
            invoice_quantity=None,
            invoice_unit=None,
            purchase_unit_interpretation=None,
            stock_unit=None,
            stock_quantity=None,
            unit_rate=None,
            cost_per_kg=None,
            source_text="",
        )
    )
    assert missing.requires_review


def test_envelope_checks_dates_pages_totals_and_keeps_source_provenance():
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(invoice(items=[rice(source_page=2)]))
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(invoice(invoice_date="2026-02-31"))
    parsed = ExtractedInvoice.model_validate(invoice(items=[rice(line_total="4500")]))
    assert parsed.warnings and parsed.items[0].requires_review
    provider = GeminiFlashProvider("synthetic-key")
    provider._request = lambda payload=None: response(invoice(items=[rice(line_total="4550")]))
    evidence = provider.infer(b"synthetic-fixture", "image/jpeg")
    draft = propose("source-id", "attempt-id", "a" * 64, evidence)
    assert draft.rows[0].fields["purchase_unit"].value == "bag"
    assert draft.rows[0].fields["units_per_purchase"].value == "50"
    assert draft.rows[0].fields["stock_quantity"].value == "100"
    assert draft.line_total_sum == "4550"
    assert draft.source_sha256 == "a" * 64 and not draft.posting_allowed
    assert draft.review_required and draft.rows[0].fields["quantity"].confidence is None


@pytest.mark.parametrize(
    "body,code",
    [
        (b"not JSON", "gemini_invalid_extraction"),
        (b"[]", "gemini_invalid_extraction"),
        (b'{"candidates": []}', "gemini_incomplete_or_blocked"),
        (response(finish="MAX_TOKENS"), "gemini_incomplete_or_blocked"),
        (response(invoice(items=[rice(stock_quantity="99")])), "gemini_invalid_extraction"),
    ],
)
def test_truncated_invalid_and_inconsistent_responses_never_become_drafts(body, code):
    provider = GeminiFlashProvider("synthetic-key")
    provider._request = lambda payload=None: body
    with pytest.raises(ProviderFailure) as caught:
        provider.infer(b"synthetic", "image/jpeg")
    assert caught.value.code == code


def test_request_is_fixed_https_bounded_schema_and_header_key(monkeypatch):
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, limit):
            assert limit == MAX_OUTPUT + 1
            return response()

    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            assert timeout == 180
            return Response()

    monkeypatch.setattr("supermarket.gemini_provider.build_opener", lambda *_: Opener())
    evidence = GeminiFlashProvider("synthetic-secret").infer(b"synthetic", "image/jpeg")
    request = captured[0]
    assert (
        request.full_url
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    )
    assert "synthetic-secret" not in request.full_url
    assert request.get_header("X-goog-api-key") == "synthetic-secret"
    payload = json.loads(request.data)
    schema = payload["generationConfig"]["responseJsonSchema"]
    assert schema["type"] == "object"
    assert "items" in schema["required"]
    assert "$defs" not in schema
    assert "invoice_quantity" in schema["properties"]["items"]["items"]["required"]
    assert "max_digits" not in json.dumps(schema)
    assert "tools" not in payload
    assert payload["contents"][0]["parts"][0]["inlineData"]["data"] == "c3ludGhldGlj"
    assert evidence.extraction is not None
    assert evidence.extraction.items[0].stock_quantity == 100


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "gemini_authentication"),
        (403, "gemini_permission"),
        (404, "gemini_model_unavailable"),
        (429, "gemini_rate_limit"),
        (500, "gemini_service_unavailable"),
    ],
)
def test_http_errors_are_sanitized(monkeypatch, status, code):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise HTTPError("https://private.invalid", status, "sensitive provider text", {}, None)

    monkeypatch.setattr("supermarket.gemini_provider.build_opener", lambda *_: Opener())
    with pytest.raises(ProviderFailure) as caught:
        GeminiFlashProvider("synthetic-secret").infer(b"fixture", "image/jpeg")
    assert caught.value.code == code
    assert "sensitive" not in str(caught.value)


def test_network_limits_readiness_and_configuration(monkeypatch):
    provider = GeminiFlashProvider("synthetic-key")
    provider._request = lambda payload=None: json.dumps(
        {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]}
    ).encode()
    assert provider.health()
    provider._request = lambda payload=None: b"[]"
    assert not provider.health()
    for key, model in [("", "gemini-3.5-flash"), ("x", "../../leak"), ("x", "gemini-pro")]:
        with pytest.raises(ValueError):
            GeminiFlashProvider(key, model)
    with pytest.raises(ValueError):
        GeminiFlashProvider("x", timeout_seconds=1)
    with pytest.raises(ProviderFailure):
        provider.infer(b"", "text/plain")

    class Opener:
        def open(self, *_args, **_kwargs):
            raise URLError("sensitive")

    monkeypatch.setattr("supermarket.gemini_provider.build_opener", lambda *_: Opener())
    assert not GeminiFlashProvider("x").health()
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    settings = Settings.from_environment()
    assert settings.gemini_api_key == "synthetic-secret"
    assert "synthetic-secret" not in repr(settings)
    monkeypatch.setenv("OCR_PROVIDER", "unknown")
    with pytest.raises(ValueError):
        Settings.from_environment()


@pytest.mark.database
def test_gemini_background_attempt_encrypted_draft_and_no_financial_posting(
    postgres_case, monkeypatch
):
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from test_finance import post, setup
    from test_ocr import upload

    from supermarket.main import create_app
    from supermarket.ocr_models import attempts, results

    monkeypatch.setattr(GeminiFlashProvider, "_request", lambda self, payload=None: response())
    monkeypatch.setattr(GeminiFlashProvider, "health", lambda self: True)
    settings = replace(
        postgres_case.settings, ocr_provider="gemini", gemini_api_key="synthetic-key"
    )
    with TestClient(create_app(settings, postgres_case.runtime)) as client:
        headers, base, path, _terminal = setup(client)
        document = upload(client, path, headers).json()
        route = path + "/ocr-documents"
        availability = client.get(route + "/provider").json()
        assert (
            availability["external_processing"]
            and availability["provider_model"] == "gemini-3.5-flash"
        )
        processed = post(
            client,
            route + "/" + document["id"] + "/process",
            headers,
            {"reason": "Synthetic Gemini contract fixture"},
        )
        assert processed.status_code == 202, processed.text
        detail = client.get(route + "/" + document["id"]).json()
        assert detail["status"] == "review_required"
        assert detail["evidence"]["extraction"]["items"][0]["stock_quantity"] == "100"
        draft = client.get(route + "/" + document["id"] + "/draft").json()
        assert draft["rows"][0]["fields"]["units_per_purchase"]["value"] == "50"
        assert not draft["posting_allowed"]
        assert client.get(path + "/purchases").json() == []
        assert client.get(path + "/stock").json() == []
        with postgres_case.admin.connect() as connection:
            assert (
                connection.execute(select(attempts.c.provider_model)).scalar_one()
                == "gemini-3.5-flash"
            )
            ciphertext = connection.execute(select(results.c.evidence_ciphertext)).scalar_one()
            assert b"Rice Kozhi" not in ciphertext
        monkeypatch.setattr(
            GeminiFlashProvider,
            "_request",
            lambda self, payload=None: response(finish="MAX_TOKENS"),
        )
        post(
            client,
            route + "/" + document["id"] + "/process",
            headers,
            {"reason": "Synthetic truncated response fixture"},
        )
        failed = client.get(route + "/" + document["id"]).json()
        assert (
            failed["status"] == "failed" and failed["error_code"] == "gemini_incomplete_or_blocked"
        )
        assert client.get(route + "/" + document["id"] + "/draft").status_code == 409
        assert (
            client.get(route + "/" + document["id"] + "/content?original=true").status_code == 200
        )


def test_numeric_json_example_uses_decimal_without_binary_float(monkeypatch):
    data = invoice(
        items=[rice(invoice_quantity=2, detected_pack_size=50, stock_quantity=100, unit_rate=2275)]
    )
    body = response(data).replace(b'\\"45.50\\"', b"45.50")
    provider = GeminiFlashProvider("synthetic")
    monkeypatch.setattr(provider, "_request", lambda payload=None: body)
    assert provider.infer(b"synthetic", "image/jpeg").extraction.items[0].cost_per_kg == Decimal(
        "45.50"
    )


def test_mass_cost_for_piece_packets_but_never_for_liquid_volume():
    packet = ExtractedItem.model_validate(
        rice(
            invoice_unit="Nos",
            purchase_unit_interpretation="PCS",
            detected_pack_size="500",
            pack_unit="G",
            stock_unit="PCS",
            stock_quantity=None,
            unit_rate="44",
            cost_per_kg=None,
        )
    )
    assert packet.stock_quantity == 2 and packet.cost_per_kg == Decimal("88.00")
    assert packet.invoice_unit == "Nos" and not packet.requires_review
    bottle = ExtractedItem.model_validate(
        rice(
            invoice_unit="Btl",
            purchase_unit_interpretation="PCS",
            detected_pack_size="500",
            pack_unit="ML",
            stock_unit="PCS",
            stock_quantity=None,
            unit_rate="14",
            cost_per_kg=None,
        )
    )
    assert bottle.stock_quantity == 2 and bottle.cost_per_kg is None
    with pytest.raises(ValidationError):
        ExtractedItem.model_validate({**bottle.model_dump(), "cost_per_kg": "28"})
    from supermarket.gemini_provider import generation_schema

    properties = generation_schema()["properties"]["items"]["items"]["properties"]
    assert properties["cost_per_kg"]["type"] == "null"
    assert properties["stock_quantity"]["type"] == "null"
