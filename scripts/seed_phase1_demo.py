"""Create an isolated synthetic UI walkthrough through the real authorized APIs."""

import argparse
import csv
import getpass
import http.cookiejar
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import NAMESPACE_URL, uuid5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--business-name", default="DEMO Phase 1 walkthrough")
    args = parser.parse_args()
    address = urlparse(args.api)
    if address.scheme not in {"http", "https"} or address.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        parser.error("This synthetic walkthrough is restricted to a local development API")
    if not args.business_name.startswith("DEMO "):
        parser.error("The business name must start with DEMO to identify synthetic data")
    password = os.environ.pop("DEMO_OWNER_PASSWORD", None) or getpass.getpass("Portal password: ")
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ""

    def call(path: str, body: dict[str, object] | None = None):
        headers = {"Content-Type": "application/json", "Origin": "http://localhost:5173"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if body is not None:
            headers["Idempotency-Key"] = str(
                uuid5(NAMESPACE_URL, path + json.dumps(body, sort_keys=True))
            )
        request = Request(  # noqa: S310 -- scheme and loopback host checked above
            args.api + "/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            with opener.open(request, timeout=15) as response:  # noqa: S310
                data = response.read()
                return json.loads(data) if data else None
        except HTTPError as error:
            raise RuntimeError(f"API {error.code} at {path}: {error.read().decode()}") from None

    login = call("/auth/login", {"email": args.email, "password": password})
    del password
    csrf = login["csrf_token"]
    try:
        if any(business["name"] == args.business_name for business in call("/businesses")):
            raise RuntimeError(
                "This demo business already exists. Select it in the UI; no data was added."
            )
        business = call(
            "/businesses",
            {
                "name": args.business_name,
                "store_name": "DEMO store",
                "store_address": "Synthetic training business; no actual shop stock",
            },
        )
        base = "/businesses/" + business["id"]
        store = call(base + "/stores")[0]["id"]
        path = base + "/stores/" + store
        terminal = call(path + "/terminals", {"name": "DEMO counter"})["id"]
        supplier = call(base + "/suppliers", {"name": "DEMO supplier", "payment_terms_days": 14})[
            "id"
        ]
        root = Path(__file__).resolve().parents[1] / "fixtures" / "demo"

        def rows(name):
            with (root / name).open(encoding="utf-8", newline="") as source:
                records = list(csv.DictReader(source))
                if any(row["data_origin"] != "synthetic" for row in records):
                    raise RuntimeError("Demo input must be explicitly synthetic")
                return records

        products = {}
        for source in rows("products.csv"):
            product = call(
                base + "/products",
                {
                    "sku": source["sku"],
                    "name": source["name"],
                    "unit": source["stock_unit"],
                    "purchase_price": source["purchase_price"],
                    "landed_cost": source["landed_cost"],
                    "selling_price": source["selling_price"],
                    "mrp": source["mrp"],
                    "gst_rate": source["gst_rate"],
                    "minimum_selling_price": "0",
                    "reorder_level": "5",
                    "reorder_quantity": "20",
                    "barcodes": [],
                    "confirm_distinct_product": True,
                },
            )
            products[source["sku"]] = product
        for source in rows("opening-stock.csv"):
            call(
                path + "/opening-stock",
                {
                    "product_id": products[source["sku"]]["id"],
                    "quantity": source["quantity"],
                    "unit_cost": source["unit_cost"],
                    "reason": source["reason"],
                    "confirmed": True,
                },
            )
        accounts = {}
        for name, kind, funds in [
            ("DEMO drawer", "cash", "1000"),
            ("DEMO UPI", "upi", "0"),
            ("DEMO bank", "bank", "2000"),
        ]:
            accounts[kind] = call(
                path + "/accounts",
                {
                    "name": name,
                    "kind": kind,
                    "opening_amount": funds,
                    "terminal_id": terminal if kind == "cash" else None,
                    "reason": "Synthetic opening funds for UI training",
                    "confirmed": True,
                },
            )["id"]
        cash_session = call(
            path + "/cash-sessions",
            {
                "account_id": accounts["cash"],
                "opening_cash": "1000",
                "reason": "Synthetic training drawer; not a physical cash count",
                "confirmed": True,
            },
        )["id"]
        sale = call(
            path + "/sales",
            {
                "terminal_id": terminal,
                "lines": [
                    {
                        "product_id": products[row["sku"]]["id"],
                        "quantity": row["quantity"],
                        "expected_price": row["unit_price"],
                        "discount": row["discount"],
                    }
                    for row in rows("sales.csv")
                ],
                "payments": [
                    {
                        "account_id": accounts["cash"],
                        "cash_session_id": cash_session,
                        "method": "cash",
                        "amount": "200",
                    },
                    {"account_id": accounts["upi"], "method": "upi", "amount": "105"},
                ],
                "confirmed": True,
            },
        )
        for reference, kind, category, amount in [
            ("DEMO-DELIVERY", "cash", "delivery", "50"),
            ("DEMO-UTILITIES", "bank", "utilities", "500"),
        ]:
            call(
                path + "/expenses",
                {
                    "account_id": accounts[kind],
                    "cash_session_id": cash_session if kind == "cash" else None,
                    "reference": reference,
                    "expense_date": "2026-10-08",
                    "category": category,
                    "description": "Synthetic training expense",
                    "amount": amount,
                    "confirmed": True,
                },
            )
        purchase = call(
            path + "/purchases",
            {
                "supplier_id": supplier,
                "invoice_number": "DEMO-STOCK-001",
                "invoice_date": "2026-10-08",
                "tax_mode": "exclusive",
                "tax_kind": "intra",
                "round_off": "0",
                "invoice_total": "960",
                "review_reason": "Synthetic carton example: 2 cartons x 24 plus 3 free pieces",
                "confirmed": True,
                "lines": [
                    {
                        "product_id": products["DEMO-SOAP"]["id"],
                        "supplier_description": "DEMO soap cartons",
                        "purchase_unit": "carton",
                        "purchase_quantity": "2",
                        "units_per_purchase": "24",
                        "free_stock_quantity": "3",
                        "conversion_evidence": (
                            "Synthetic source: 24 pieces per carton plus 3 free pieces"
                        ),
                        "unit_rate": "480",
                        "discount": "0",
                        "gst_rate": "0",
                    }
                ],
            },
        )
        call(
            path + "/supplier-payments",
            {
                "purchase_id": purchase["id"],
                "account_id": accounts["bank"],
                "amount": "200",
                "reference": "DEMO-SUPPLIER-PAY",
                "reason": "Synthetic partial supplier payment",
                "confirmed": True,
            },
        )
        print("Created " + business["name"] + ". Select this separate business in the portal.")
        print("Synthetic receipt " + sale["invoice_number"] + ": INR 305, cash 200 + UPI 105.")
        print("Balances: drawer 1150, UPI 105, bank 1300; supplier invoice outstanding 760.")
        print("Stock: biscuit 37 pcs, rice 17.5 kg, soap 79 pcs. Drawer stays open for practice.")
        print("No original bills, barcode values, refunds or cash closing were imported.")
    finally:
        call("/auth/logout", {})


if __name__ == "__main__":
    main()
