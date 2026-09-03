"""
HAR recorder and probe list for DAST scans of ansible-chatbot-stack.

The POST /v1/query and /v1/streaming_query payloads match
tests/sanity/test_providers.py (TestChatbotSanity). Extra GET probes cover
Lightspeed Stack surfaces that the sanity suite does not hit, without needing
MCP servers.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qsl

import requests


DEFAULT_HAR_NAME = "chatbot-requests.har"
QUERY_PATH = "/v1/query"
JSON_MIME = "application/json"


@dataclass(frozen=True)
class Probe:
    method: str
    path: str
    json_body: dict | None = None
    stream: bool = False
    timeout: int = 10


def granite_probes(provider_config):
    """Return the HTTP probes to record for a granite DAST run."""
    query = {
        "query": "What is AAP?",
        "model": provider_config["model"],
        "provider": provider_config["provider"],
    }
    empty_query = {**query, "query": ""}
    ansible_query = {**query, "query": "What is Ansible?"}
    return [
        Probe("GET", "/v1/config"),
        Probe("GET", "/v1/models"),
        Probe("POST", QUERY_PATH, json_body=query, timeout=120),
        Probe("POST", "/v1/streaming_query", json_body=query, stream=True, timeout=120),
        Probe("POST", QUERY_PATH, json_body=empty_query, timeout=120),
        Probe("POST", QUERY_PATH, json_body=ansible_query, timeout=120),
        Probe("GET", "/"),
        Probe("GET", "/openapi.json"),
        Probe("GET", "/v1/info"),
        Probe("GET", "/v1/tools"),
        Probe("GET", "/v1/shields"),
        Probe("GET", "/v1/providers"),
        Probe("GET", "/v1/prompts"),
        Probe("GET", "/v1/rags"),
        Probe("GET", "/v1/feedback/status"),
        Probe("GET", "/v1/conversations"),
        Probe("GET", "/readiness"),
        Probe("GET", "/liveness"),
        Probe("GET", "/metrics"),
    ]


def _header_list(headers):
    if headers is None:
        return []
    if hasattr(headers, "items"):
        return [{"name": str(name), "value": str(value)} for name, value in headers.items()]
    return [{"name": str(name), "value": str(value)} for name, value in headers]


def _query_string(url):
    return [{"name": name, "value": value} for name, value in parse_qsl(urlparse(url).query)]


def _content_block(body, content_type):
    mime = content_type or "application/octet-stream"
    size = len(body)
    try:
        return {"size": size, "mimeType": mime, "text": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "size": size,
            "mimeType": mime,
            "text": base64.b64encode(body).decode("ascii"),
            "encoding": "base64",
        }


class HarRecorder:
    """Capture requests/responses as a HAR 1.2 log for RapiDAST/ZAP import."""

    def __init__(self, creator_name="ansible-chatbot-stack DAST", creator_version="1.0.0"):
        self.entries = []
        self.creator_name = creator_name
        self.creator_version = creator_version

    def add_entry(
        self,
        method,
        url,
        status,
        reason,
        started,
        duration_ms,
        request_headers=None,
        request_body=b"",
        request_mime=JSON_MIME,
        response_headers=None,
        response_body=b"",
        response_mime=JSON_MIME,
    ):
        request_body = request_body or b""
        response_body = response_body or b""
        request = {
            "method": method,
            "url": url,
            "httpVersion": "HTTP/1.1",
            "headers": _header_list(request_headers),
            "queryString": _query_string(url),
            "headersSize": -1,
            "bodySize": len(request_body),
        }
        if request_body or method not in ("GET", "HEAD"):
            request["postData"] = {
                "mimeType": request_mime,
                "text": request_body.decode("utf-8", errors="replace"),
            }
        self.entries.append(
            {
                "startedDateTime": started.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "time": duration_ms,
                "request": request,
                "response": {
                    "status": status,
                    "statusText": reason,
                    "httpVersion": "HTTP/1.1",
                    "headers": _header_list(response_headers),
                    "content": _content_block(response_body, response_mime),
                    "redirectURL": "",
                    "headersSize": -1,
                    "bodySize": len(response_body),
                },
                "cache": {},
                "timings": {"send": 0, "wait": duration_ms, "receive": 0},
            }
        )

    def request(self, method, url, json_body=None, stream=False, timeout=10, headers=None):
        req_headers = {"User-Agent": "ansible-chatbot-stack-dast/1.0"}
        if headers:
            req_headers.update(headers)
        request_body = b""
        request_mime = JSON_MIME
        if json_body is not None:
            request_body = json_dumps_bytes(json_body)
            req_headers.setdefault("Content-Type", JSON_MIME)
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        response = requests.request(
            method,
            url,
            data=request_body or None,
            headers=req_headers,
            stream=stream,
            timeout=timeout,
        )
        if stream:
            response_body = b"".join(response.iter_content(chunk_size=8192))
        else:
            response_body = response.content
        duration_ms = (time.perf_counter() - t0) * 1000
        self.add_entry(
            method=method.upper(),
            url=response.url or url,
            status=response.status_code,
            reason=response.reason or "",
            started=started,
            duration_ms=duration_ms,
            request_headers=req_headers,
            request_body=request_body,
            request_mime=req_headers.get("Content-Type", request_mime),
            response_headers=response.headers,
            response_body=response_body,
            response_mime=response.headers.get("Content-Type", "application/octet-stream"),
        )
        print(f"[DAST] {method.upper()} {url} -> HTTP {response.status_code}", flush=True)
        return response

    def to_har(self):
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": self.creator_name, "version": self.creator_version},
                "entries": self.entries,
            }
        }

    def save(self, path, redact_values=None):
        payload = self.to_har()
        secrets = [value for value in (redact_values or []) if value]
        if secrets:
            payload = _redact(payload, secrets)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")


def json_dumps_bytes(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


REDACTED = "***REDACTED***"


def _redact(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact(val, secrets) for key, val in value.items()}
    return value


def run_probes(recorder, base_url, provider_config):
    """Issue granite DAST probes and record them. Returns the list of responses."""
    responses = []
    for probe in granite_probes(provider_config):
        url = f"{base_url.rstrip('/')}{probe.path}"
        responses.append(
            recorder.request(
                probe.method,
                url,
                json_body=probe.json_body,
                stream=probe.stream,
                timeout=probe.timeout,
            )
        )
    return responses
