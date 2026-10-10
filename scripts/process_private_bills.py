"""Run real OCR for private inbox originals; never posts stock/accounts/payments."""

import argparse
import getpass
import http.cookiejar
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

from supermarket.ocr_provider import NoRedirect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--business", default="Phase 2 original-bill review")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--origin", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--use-current-provider",
        action="store_true",
        help="Re-extract completed bills when their model differs from the configured provider",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit real bill attempts; 0 processes all"
    )
    args = parser.parse_args()
    address = urlparse(args.url)
    if (
        address.scheme != "http"
        or address.hostname not in {"127.0.0.1", "localhost"}
        or address.path
        or address.query
        or address.fragment
        or address.username
    ):
        parser.error("Original bill processing requires a trusted local application root")
    password = os.environ.pop("BILL_IMPORT_PASSWORD", None) or getpass.getpass("Portal password: ")
    opener = build_opener(
        HTTPCookieProcessor(http.cookiejar.CookieJar()), ProxyHandler({}), NoRedirect()
    )
    csrf = ""

    def call(path, body=None, key=None):
        headers = {"Origin": args.origin}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if key:
            headers["Idempotency-Key"] = key
        request = Request(  # noqa: S310 -- validated local application
            args.url + "/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={**headers, "Content-Type": "application/json"},
        )
        try:
            with opener.open(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read()) if response.status != 204 else None
        except HTTPError as error:
            raise RuntimeError(
                f"Private OCR API returned HTTP {error.code}; no source data logged"
            ) from None

    session = call("/auth/login", {"email": args.email, "password": password})
    csrf = session["csrf_token"]
    try:
        business = next(
            (item for item in call("/businesses") if item["name"] == args.business), None
        )
        if business is None:
            raise RuntimeError("Select an existing private original-bill business")
        base = "/businesses/" + business["id"]
        store = call(base + "/stores")[0]
        route = base + "/stores/" + store["id"] + "/ocr-documents"
        provider = call(route + "/provider")
        if not provider["health_verified"]:
            raise RuntimeError("The selected invoice extraction provider is not ready")
        documents = []
        offset = 0
        while True:
            page = call(route + "?limit=200&offset=" + str(offset))
            documents.extend(item for item in page if item["data_origin"] == "authentic")
            if len(page) < 200:
                break
            offset += 200
        print(
            f"Starting private OCR for {len(documents)} originals; posting is disabled.", flush=True
        )
        if args.limit > 0:
            documents = documents[: args.limit]
        completed = 0
        for index, document in enumerate(documents, 1):
            detail_route = route + "/" + document["id"]
            current = call(detail_route)
            previous_model = (current.get("evidence") or {}).get("provider_model")
            if current["status"] != "review_required" or (
                args.use_current_provider and previous_model != provider["provider_model"]
            ):
                if current["status"] != "processing":
                    current = call(
                        detail_route + "/process",
                        {
                            "reason": (
                                "Owner requested extraction with the configured provider; "
                                "all financial values require review"
                            )
                        },
                        str(uuid4()),
                    )
                deadline = time.monotonic() + 7260
                while current["status"] == "processing" and time.monotonic() < deadline:
                    time.sleep(5)
                    current = call(detail_route)
            previous_model = (current.get("evidence") or {}).get("provider_model")
            if current["status"] != "review_required" or (
                args.use_current_provider and previous_model != provider["provider_model"]
            ):
                print(
                    f"Original {index}/{len(documents)}: {current['status']}; "
                    "source retained; review required.",
                    flush=True,
                )
                if current.get("error_code") == "gemini_rate_limit":
                    print(
                        "Gemini quota reached; batch stopped. Existing drafts are retained.",
                        flush=True,
                    )
                    break
                continue
            draft = call(detail_route + "/draft")
            completed += 1
            print(
                f"Original {index}/{len(documents)}: genuine OCR complete, "
                f"{len(draft['rows'])} proposed rows; unreviewed.",
                flush=True,
            )
        print(
            f"Validated drafts checked: {completed}/{len(documents)}. "
            "Stock, payables and cash/bank were not changed.",
            flush=True,
        )
        if completed != len(documents):
            raise SystemExit(2)
    finally:
        call("/auth/logout", {})


if __name__ == "__main__":
    main()
