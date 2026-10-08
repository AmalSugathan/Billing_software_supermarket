"""Synthetic HTTP evidence: enforce response limits and reject unsafe endpoints."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from supermarket.ocr_provider import MAX_OUTPUT, PaddleLayoutProvider, ProviderFailure


@pytest.mark.parametrize(
    "body,code",
    [
        (b"not JSON", "invalid_response"),
        (json.dumps({"errorCode": 1}).encode(), "invalid_response"),
        (
            json.dumps({"errorCode": 0, "result": {"layoutParsingResults": []}}).encode(),
            "invalid_response",
        ),
        (
            json.dumps(
                {
                    "errorCode": 0,
                    "result": {
                        "layoutParsingResults": [{"prunedResult": {}, "markdown": {"text": ""}}]
                    },
                }
            ).encode(),
            "no_text",
        ),
        (b"x" * (MAX_OUTPUT + 1), "output_limit"),
    ],
    ids=["invalid-json", "provider-error", "no-pages", "no-text", "oversized-output"],
)
def test_provider_rejects_unusable_or_oversized_response(body, code):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ProviderFailure) as caught:
            PaddleLayoutProvider(f"http://127.0.0.1:{server.server_port}").infer(
                b"synthetic", "image/jpeg"
            )
        assert caught.value.code == code
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_provider_never_follows_redirects():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/private-evidence")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ProviderFailure):
            PaddleLayoutProvider(f"http://127.0.0.1:{server.server_port}").infer(
                b"synthetic", "image/jpeg"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            {"status": "ready", "provider_model": "PaddleOCR-VL-1.6", "pipeline_version": "v1.6"},
            True,
        ),
        ({"status": "ready", "provider_model": "other", "pipeline_version": "v1.6"}, False),
        (
            {"status": "loading", "provider_model": "PaddleOCR-VL-1.6", "pipeline_version": "v1.6"},
            False,
        ),
        ({"status": "ready", "provider_model": "PaddleOCR-VL-1.6"}, False),
    ],
)
def test_readiness_requires_full_pipeline_identity(payload, expected):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == "/health/ready"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert PaddleLayoutProvider(f"http://127.0.0.1:{server.server_port}").health() is expected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
