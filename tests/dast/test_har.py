"""Unit tests for DAST HAR recording (no live chatbot or LLM)."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

import pytest

from tests.dast.har import HarRecorder, granite_probes, json_dumps_bytes, run_probes


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = b'data: {"event":"token","data":{"token":"AAP"}}\n\n'
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    thread.join(timeout=5)


def test_granite_probes_include_sanity_endpoints():
    probes = granite_probes({"model": "granite/demo", "provider": "granite"})
    paths = [(p.method, p.path) for p in probes]
    assert ("GET", "/v1/config") in paths
    assert ("GET", "/v1/models") in paths
    assert ("POST", "/v1/query") in paths
    assert ("POST", "/v1/streaming_query") in paths
    streaming = next(p for p in probes if p.path == "/v1/streaming_query")
    assert streaming.stream is True
    assert streaming.json_body["query"] == "What is AAP?"
    assert streaming.json_body["model"] == "granite/demo"
    queries = [p.json_body["query"] for p in probes if p.path == "/v1/query"]
    assert "What is AAP?" in queries
    assert "" in queries
    assert "What is Ansible?" in queries


def test_har_recorder_round_trip(tmp_path, http_server):
    recorder = HarRecorder()
    get_resp = recorder.request("GET", f"{http_server}/v1/config")
    post_resp = recorder.request(
        "POST",
        f"{http_server}/v1/query",
        json_body={"query": "What is AAP?", "model": "granite/demo", "provider": "granite"},
        timeout=5,
    )
    stream_resp = recorder.request(
        "POST",
        f"{http_server}/v1/streaming_query",
        json_body={"query": "What is AAP?"},
        stream=True,
        timeout=5,
    )
    assert get_resp.status_code == 200
    assert post_resp.status_code == 200
    assert stream_resp.status_code == 200

    har_path = tmp_path / "chatbot-requests.har"
    recorder.save(har_path)
    har = json.loads(har_path.read_text())
    assert har["log"]["version"] == "1.2"
    assert len(har["log"]["entries"]) == 3
    get_entry = har["log"]["entries"][0]
    assert get_entry["request"]["method"] == "GET"
    assert get_entry["response"]["status"] == 200
    assert get_entry["response"]["content"]["text"] == '{"ok":true}'
    post_entry = har["log"]["entries"][1]
    assert post_entry["request"]["method"] == "POST"
    assert "What is AAP?" in post_entry["request"]["postData"]["text"]
    stream_entry = har["log"]["entries"][2]
    assert "token" in stream_entry["response"]["content"]["text"]


def test_run_probes_records_every_spec(http_server):
    recorder = HarRecorder()
    config = {"model": "granite/demo", "provider": "granite"}
    responses = run_probes(recorder, http_server, config)
    assert len(responses) == len(granite_probes(config))
    assert len(recorder.entries) == len(responses)
    assert all(item.status_code == 200 for item in responses)


def test_json_dumps_bytes_is_compact():
    assert json_dumps_bytes({"a": 1}) == b'{"a":1}'
