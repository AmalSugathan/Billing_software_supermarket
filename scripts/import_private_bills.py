"""Opt-in local document intake: originals never create purchases or stock movements."""

import argparse
import getpass
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import NAMESPACE_URL, uuid5

from supermarket.ocr_provider import NoRedirect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--origin", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    endpoint = urlparse(args.url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"127.0.0.1", "localhost"}
        or endpoint.path
        or endpoint.query
        or endpoint.fragment
        or endpoint.username
    ):
        parser.error("Private import requires a local HTTP application root")
    source = args.source.resolve(strict=True)
    if not source.is_dir():
        parser.error("Source must be a private bill directory")
    files = sorted(
        file
        for file in source.iterdir()
        if file.is_file() and file.suffix.lower() in {".jpg", ".jpeg", ".png", ".pdf"}
    )
    if not files:
        parser.error("No supported original bills found")
    password = os.environ.get("BILL_IMPORT_PASSWORD") or getpass.getpass("Portal password: ")
    opener = build_opener(
        HTTPCookieProcessor(http.cookiejar.CookieJar()), ProxyHandler({}), NoRedirect()
    )
    csrf = ""

    def call(path, *, body=None, raw=None, mime=None, key=None):
        headers = {"Origin": args.origin}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if key:
            headers["Idempotency-Key"] = str(key)
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = mime or "application/json"
        request = Request(  # noqa: S310 -- fixed validated loopback application
            args.url + "/api/v1" + path, data=data, headers=headers
        )
        with opener.open(request, timeout=30) as response:  # noqa: S310 -- local validated application
            return json.loads(response.read()) if response.status != 204 else None

    session = call("/auth/login", body={"email": args.email, "password": password})
    csrf = session["csrf_token"]
    try:
        name = "Phase 2 original-bill review"
        businesses = call("/businesses")
        business = next((item for item in businesses if item["name"] == name), None)
        if business is None:
            business = call(
                "/businesses",
                body={
                    "name": name,
                    "store_name": "Original invoice review",
                    "store_address": (
                        "Historical evidence review only; no opening stock or financial import"
                    ),
                },
            )
        base = "/businesses/" + business["id"]
        store = call(base + "/stores")[0]
        route = base + "/stores/" + store["id"] + "/ocr-documents"
        if not call(route + "/provider")["upload_enabled"]:
            raise RuntimeError("Configure private OCR encryption before importing")
        existing = set()
        offset = 0
        while True:
            records = call(route + "?limit=200&offset=" + str(offset))
            existing.update(item["sha256"] for item in records)
            if len(records) < 200:
                break
            offset += 200
        saved = skipped = 0
        for file in files:
            if file.stat().st_size > 10 * 1024 * 1024:
                raise RuntimeError("A source exceeds the 10 MiB intake limit")
            content = file.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest in existing:
                skipped += 1
                continue
            mime = (
                "application/pdf"
                if file.suffix.lower() == ".pdf"
                else "image/png"
                if file.suffix.lower() == ".png"
                else "image/jpeg"
            )
            key = uuid5(NAMESPACE_URL, business["id"] + store["id"] + digest + file.name)
            try:
                result = call(
                    route + "?filename=" + quote(file.name) + "&data_origin=authentic",
                    raw=content,
                    mime=mime,
                    key=key,
                )
            except HTTPError as error:
                raise RuntimeError(
                    f"Private upload failed with HTTP {error.code}; no document content logged"
                ) from None
            if result["status"] != "uploaded":
                raise RuntimeError("Unexpected intake status; inspect the private inbox")
            saved += 1
            existing.add(digest)
        print(
            f"Private original-bill inbox: {saved} uploaded; {skipped} already present. "
            "No purchases or stock posted."
        )
    finally:
        call("/auth/logout", body={})


if __name__ == "__main__":
    main()
