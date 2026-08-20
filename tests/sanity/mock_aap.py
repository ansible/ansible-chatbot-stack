"""
Minimal mock AAP Gateway/Controller/Lightspeed HTTP server for MCP sanity tests.

MCP servers list tools from their bundled OpenAPI specs and only need AAP for:
  - token validation via GET /api/gateway/v1/me/
  - optional JWT key via GET /api/gateway/v1/jwt_key/
  - actual tool HTTP calls (which this mock records and answers with 404)
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


SANITY_USERNAME = "sanity-user"

# Placeholder PEM so AAPJWTValidator can fetch a key if a JWT header is sent.
_DUMMY_JWT_KEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu1SU1LfVLPHCozMxH2Mo\n"
    "4lgOEePzNm0tRgeLezU6ZChjAYR9TAocyc6onMAJfRXGdoUE6TAupqzlsjvnW+S\n"
    "nvKQ2+bPQxbtmRU77kI0xT2sGacCewdDFQ==\n"
    "-----END PUBLIC KEY-----\n"
)

_GATEWAY_ME_PATHS = {"/api/gateway/v1/me", "/api/gateway/v1/me/"}
_GATEWAY_JWT_PATHS = {"/api/gateway/v1/jwt_key", "/api/gateway/v1/jwt_key/"}


class MockAAPHandler(BaseHTTPRequestHandler):
    """HTTP handler that authenticates any token and 404s tool calls."""

    def log_message(self, format, *args):  # noqa: A003
        """Keep pytest output readable; recorded requests live on the server."""

    def _record(self):
        entry = {
            "method": self.command,
            "path": urlparse(self.path).path,
            "query": urlparse(self.path).query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
        }
        with self.server.log_lock:
            self.server.request_log.append(entry)

    def _send_json(self, status, payload, write_body=True):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def _send_text(self, status, text, content_type="text/plain", write_body=True):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def _handle(self, write_body=True):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        self._record()
        path = urlparse(self.path).path
        if path in _GATEWAY_ME_PATHS:
            self._send_json(200, {"results": [{"username": SANITY_USERNAME}]}, write_body=write_body)
            return
        if path in _GATEWAY_JWT_PATHS:
            self._send_text(200, _DUMMY_JWT_KEY, write_body=write_body)
            return
        self._send_json(404, {"detail": "mock AAP has no such resource"}, write_body=write_body)

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle(write_body=False)

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def do_OPTIONS(self):
        self._handle()


class MockAAPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address):
        super().__init__(server_address, MockAAPHandler)
        self.request_log = []
        self.log_lock = threading.Lock()


class MockAAP:
    """Lifecycle wrapper around MockAAPServer."""

    def __init__(self, server: MockAAPServer, thread: threading.Thread):
        self._server = server
        self._thread = thread

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def request_log(self) -> list[dict]:
        with self._server.log_lock:
            return list(self._server.request_log)

    def tool_requests(self) -> list[dict]:
        """Requests that are not gateway auth/JWT lookups."""
        skip = _GATEWAY_ME_PATHS | _GATEWAY_JWT_PATHS
        return [entry for entry in self.request_log if entry["path"] not in skip]

    def clear(self):
        with self._server.log_lock:
            self._server.request_log.clear()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def start_mock_aap(port: int | None = None) -> MockAAP:
    """
    Start the mock AAP on 127.0.0.1.

    If port is 0 or None, bind an ephemeral port (None uses MCP_AAP_MOCK_PORT
    when provided by the caller).
    """
    bind_port = 0 if port is None else port
    try:
        server = MockAAPServer(("127.0.0.1", bind_port))
    except OSError as exc:
        raise RuntimeError(f"Failed to bind mock AAP on port {bind_port}: {exc}") from exc

    thread = threading.Thread(target=server.serve_forever, name="mock-aap", daemon=True)
    thread.start()
    return MockAAP(server, thread)


def port_is_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if a TCP port already accepts connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0
