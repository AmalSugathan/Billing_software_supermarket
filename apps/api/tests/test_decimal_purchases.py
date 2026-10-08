from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from supermarket.purchases import PurchaseInput, calculate, signed_round_off


def data(**changes):
    product_id = str(uuid4())
    payload = {
        "supplier_id": str(uuid4()),
        "invoice_number": "DEMO",
        "invoice_date": "2026-10-08",
        "tax_mode": "exclusive",
        "tax_kind": "intra",
        "invoice_total": "105.00",
        "review_reason": "Synthetic calculation",
        "lines": [
            {
                "product_id": product_id,
                "supplier_description": "DEMO",
                "purchase_unit": "carton",
                "purchase_quantity": "2",
                "units_per_purchase": "24",
                "free_stock_quantity": "2",
                "conversion_evidence": "Synthetic 24 units per carton",
                "unit_rate": "50.00",
                "gst_rate": "5",
            }
        ],
        **changes,
    }
    catalog = {
        product_id: {
            "name": "DEMO",
            "sku": "DEMO",
            "unit": "pcs",
            "active": True,
            "batch_tracking": False,
            "expiry_tracking": False,
        }
    }
    return payload, catalog


def test_exact_pack_free_units_inclusive_interstate_and_signed_rounding():
    payload, catalog = data()
    calculated = calculate(PurchaseInput.model_validate(payload), catalog)
    assert calculated.lines[0].stock_quantity == 50
    assert calculated.lines[0].unit_cost == Decimal("2.000000")
    assert calculated.cgst == calculated.sgst == Decimal("2.50")
    payload.update(tax_mode="inclusive", tax_kind="inter", invoice_total="100.00")
    inclusive = calculate(PurchaseInput.model_validate(payload), catalog)
    assert inclusive.taxable_total == Decimal("95.24")
    assert inclusive.igst == Decimal("4.76") and inclusive.cgst == 0
    payload.update(round_off="-0.01", invoice_total="99.99")
    assert calculate(PurchaseInput.model_validate(payload), catalog).invoice_total == Decimal(
        "99.99"
    )
    for value in (None, 0.1, "NaN", "1e0", "1.01", "-2"):
        with pytest.raises(ValueError):
            signed_round_off(value)


def test_quantity_amount_and_conversion_bounds_reject_without_rounding():
    payload, catalog = data()
    line = payload["lines"][0]
    for changes in (
        {"units_per_purchase": "999999999999.999"},
        {"purchase_unit": "kg", "purchase_quantity": "0.001", "units_per_purchase": "0.001"},
        {"unit_rate": "999999999999.999999"},
        {"discount": "101"},
    ):
        candidate = {**payload, "lines": [{**line, **changes}]}
        with pytest.raises(HTTPException):
            calculate(PurchaseInput.model_validate(candidate), catalog)
    for changes in ({"lines": []}, {"confirmed": "yes"}, {"invoice_total": 105.0}):
        with pytest.raises(ValidationError):
            PurchaseInput.model_validate({**payload, **changes})
    with pytest.raises(HTTPException):
        calculate(PurchaseInput.model_validate(payload), {})
    catalog[line["product_id"]]["active"] = False
    with pytest.raises(HTTPException):
        calculate(PurchaseInput.model_validate(payload), catalog)


def test_unit_cost_and_invoice_overflow_are_validated():
    payload, catalog = data()
    product_id = payload["lines"][0]["product_id"]
    catalog[product_id]["unit"] = "kg"
    payload["lines"][0].update(
        purchase_unit="kg",
        purchase_quantity="0.001",
        units_per_purchase="1",
        free_stock_quantity="0",
        unit_rate="999999999.999999",
        gst_rate="0",
    )
    payload["invoice_total"] = "1000000.00"
    assert calculate(PurchaseInput.model_validate(payload), catalog).invoice_total == Decimal(
        "1000000.00"
    )
    payload["lines"][0].update(
        purchase_unit="bag",
        purchase_quantity="1",
        units_per_purchase="0.001",
        unit_rate="999999999999.99",
    )
    payload["invoice_total"] = "999999999999.99"
    with pytest.raises(HTTPException):
        calculate(PurchaseInput.model_validate(payload), catalog)
    payload, catalog = data(invoice_total="0", lines=[])
    with pytest.raises(ValidationError):
        PurchaseInput.model_validate(payload)
