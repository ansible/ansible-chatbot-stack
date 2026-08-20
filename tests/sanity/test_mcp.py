"""
MCP server sanity tests for ansible-chatbot-stack.

Starts real controller and lightspeed MCP servers (from quay.io/ansible/ansible-mcp-*)
against a mock AAP. Tool calls are expected to fail with 404; the suite asserts
tool filtering and that the chatbot stays healthy.

Requires an inference provider (same env vars as the other sanity suites) and
pullable MCP images (skipped if pull fails).

Examples:
    make test-sanity-mcp
    pytest tests/sanity/ -v -m mcp
    pytest tests/sanity/ -v -m "mcp and granite"
    pytest tests/sanity/ -v -m mcp --mcp-debug
    MCP_DEBUG=1 make test-sanity-mcp
"""

from __future__ import annotations

import ast
import json
import socket

import pytest
import requests

from tests.sanity.conftest import _CHATBOT_OUTPUT_LOCK, MCP_AUTH_TOKEN

MCP_HEADERS = json.dumps(
    {
        "mcp::aap-controller": {"X-Authorization": MCP_AUTH_TOKEN},
        "mcp::aap-lightspeed": {"X-Authorization": MCP_AUTH_TOKEN},
    }
)

_CONTROLLER_HINTS = ("job_template", "job_templates", "workflow_job_template", "inventories")
_LIGHTSPEED_HINTS = (
    "health_status",
    "health_retrieve",
    "health_status_chatbot",
    "contentmatches",
    "me_summary",
    "me_token",
    "wca_api",
    "wca_model",
    "check_status",
    "check_retrieve",
    "explanations",
)


def _query_headers():
    return {
        "Content-Type": "application/json",
        "MCP-HEADERS": MCP_HEADERS,
    }


def _post_query(base_url, provider_setup, query, timeout=180):
    payload = {
        "query": query,
        "model": provider_setup["model"],
        "provider": provider_setup["provider"],
    }
    return requests.post(
        f"{base_url}/v1/query",
        json=payload,
        headers=_query_headers(),
        timeout=timeout,
    )


def _response_text(response_data):
    return (
        response_data.get("response")
        or response_data.get("answer")
        or response_data.get("text")
        or response_data.get("result", "")
    )


def _joined_logs(output_lines):
    return "\n".join(output_lines)


def _filtered_tool_names(output_lines):
    """Parse 'Filtered tool names from LLM: [...]' lines from chatbot logs."""
    names = []
    for line in output_lines:
        if "Filtered tool names from LLM:" not in line:
            continue
        _, _, rest = line.partition("Filtered tool names from LLM:")
        rest = rest.strip()
        try:
            parsed = ast.literal_eval(rest)
        except (ValueError, SyntaxError):
            names.append(rest.lower())
            continue
        if isinstance(parsed, list):
            names.extend(str(item) for item in parsed)
        else:
            names.append(str(parsed))
    return names


def _always_included_tools(output_lines):
    """
    Parse 'Always included tools (config + previously called): {...}' lines.

    knowledge_search is injected here, outside the LLM-driven filter step, so it
    never appears in 'Filtered tool names from LLM:' — that line only carries the
    subset of MCP tools the LLM chose to keep, which is legitimately empty ([])
    when a query needs no controller/lightspeed tool.
    """
    marker = "Always included tools (config + previously called):"
    for line in output_lines:
        if marker not in line:
            continue
        _, _, rest = line.partition(marker)
        rest = rest.strip()
        try:
            parsed = ast.literal_eval(rest)
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, (set, list, tuple)):
            return {str(item) for item in parsed}
        return {str(parsed)}
    return set()


def _filtering_enabled_count(output_lines):
    """Return the largest 'filtering N tools' count logged by the inline agent."""
    counts = []
    marker = "Tool filtering enabled - filtering "
    for line in output_lines:
        if marker not in line:
            continue
        after = line.split(marker, 1)[1]
        digits = []
        for char in after:
            if char.isdigit():
                digits.append(char)
            elif digits:
                break
        if digits:
            counts.append(int("".join(digits)))
    return max(counts) if counts else 0


def _unique_names(names):
    seen = set()
    unique = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique


def _first_int_after(marker, output_lines):
    """Return the first integer after marker, or 0 if none."""
    for line in output_lines:
        if marker not in line:
            continue
        after = line.split(marker, 1)[1]
        digits = []
        for char in after:
            if char.isdigit():
                digits.append(char)
            elif digits:
                break
        if digits:
            return int("".join(digits))
    return 0


def format_filter_debug(output_lines, query=""):
    """Build a one-screen summary of how many tools the filter kept."""
    logs = _joined_logs(output_lines)
    before = _filtering_enabled_count(output_lines)
    names = _unique_names(_filtered_tool_names(output_lines))
    after = len(names)
    header = f"[MCP filter] query={query!r}" if query else "[MCP filter]"
    if "Skipping tool filtering" in logs and after == 0:
        catalog = _first_int_after("Skipping tool filtering - ", output_lines)
        catalog_s = catalog if catalog else "n/a"
        return f"{header}\n  skipped (at or below min_tools; catalog={catalog_s})"
    fewer = before - after if before else "n/a"
    before_s = before if before else "n/a"
    preview = ", ".join(names[:15]) or "(none parsed from logs)"
    if len(names) > 15:
        preview += f", ... (+{len(names) - 15} more)"
    return (
        f"{header}\n"
        f"  {before_s} tools in → {after} kept ({fewer} fewer)\n"
        f"  kept: {preview}"
    )


def _lines_len(output_lines):
    with _CHATBOT_OUTPUT_LOCK:
        return len(output_lines)


def _lines_from(output_lines, start):
    with _CHATBOT_OUTPUT_LOCK:
        return list(output_lines[start:])


def _tcp_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


class TestMCPSanity:
    """Sanity tests for MCP servers + lightspeed_inline_agent tool filtering."""

    def test_mcp_sse_endpoints_reachable(self, mcp_provider_setup):
        assert _tcp_open(8004), "MCP controller is not listening on 8004"
        assert _tcp_open(8005), "MCP lightspeed is not listening on 8005"

    def test_server_health(self, base_url, mcp_provider_setup):
        response = requests.get(f"{base_url}/v1/config", timeout=10)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        config_data = response.json()
        assert config_data is not None
        assert len(config_data) > 0
        config_text = json.dumps(config_data).lower()
        assert "mcp" in config_text or "aap-controller" in config_text, (
            "MCP config not found in /v1/config response"
        )

    def test_realistic_tool_lists_trigger_filtering(
        self, base_url, mcp_provider_setup, mcp_filter_debug
    ):
        """Controller+Lightspeed tool lists exceed min_tools, so filtering must run."""
        mock_aap = mcp_provider_setup["mock_aap"]
        output_lines = mcp_provider_setup["output_lines"]
        start = _lines_len(output_lines)
        mock_aap.clear()
        response = _post_query(
            base_url,
            mcp_provider_setup,
            "What is AAP?",
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        new_lines = _lines_from(output_lines, start)
        count = _filtering_enabled_count(new_lines)
        logs = _joined_logs(new_lines)
        assert "Skipping MCP server" not in logs, (
            "MCP servers were skipped because authorization headers did not resolve. "
            "mcp-lightspeed-stack.yaml must set authorization_headers to 'client' "
            "(or a token file path), and queries must send MCP-HEADERS. "
            f"Recent logs:\n{logs[-2000:]}"
        )
        mcp_filter_debug(new_lines, "What is AAP?")
        assert count > 10 or "Filtered tool names from LLM:" in logs, (
            "Expected lightspeed_inline_agent to filter a large MCP tool list. "
            f"filtering count={count}. Recent logs:\n{logs[-2000:]}"
        )

    def test_tool_filtering_controller_query(
        self, base_url, mcp_provider_setup, mcp_filter_debug
    ):
        mock_aap = mcp_provider_setup["mock_aap"]
        output_lines = mcp_provider_setup["output_lines"]
        start = _lines_len(output_lines)
        mock_aap.clear()
        response = _post_query(
            base_url,
            mcp_provider_setup,
            "List the job templates available in automation controller.",
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        new_lines = _lines_from(output_lines, start)
        names = [n.lower() for n in _filtered_tool_names(new_lines)]
        joined_names = " ".join(names)
        tool_paths = [entry["path"].lower() for entry in mock_aap.tool_requests()]

        family_hit = (
            any(hint in joined_names for hint in _CONTROLLER_HINTS)
            or any("job_template" in path or "/api/v2/" in path for path in tool_paths)
        )
        mcp_filter_debug(
            new_lines, "List the job templates available in automation controller."
        )
        assert family_hit, (
            "Expected controller tool family (e.g. job_templates) in the filtered tool "
            f"list or mock AAP. filtered={names[:20]!r} mock_paths={tool_paths!r}"
        )
        assert not any(hint in joined_names for hint in _LIGHTSPEED_HINTS), (
            "Lightspeed tool family hints leaked into a controller-only filter result: "
            f"filtered={names[:20]!r}"
        )

    def test_tool_filtering_lightspeed_query(
        self, base_url, mcp_provider_setup, mcp_filter_debug
    ):
        mock_aap = mcp_provider_setup["mock_aap"]
        output_lines = mcp_provider_setup["output_lines"]
        start = _lines_len(output_lines)
        mock_aap.clear()
        response = _post_query(
            base_url,
            mcp_provider_setup,
            "Check the Ansible Lightspeed health status and chatbot health.",
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        new_lines = _lines_from(output_lines, start)
        names = [n.lower() for n in _filtered_tool_names(new_lines)]
        joined_names = " ".join(names)
        tool_paths = [entry["path"].lower() for entry in mock_aap.tool_requests()]

        family_hit = (
            any(hint in joined_names for hint in _LIGHTSPEED_HINTS)
            or any(
                path.startswith("/api/v1/") or path.startswith("/check")
                for path in tool_paths
            )
        )
        mcp_filter_debug(
            new_lines,
            "Check the Ansible Lightspeed health status and chatbot health.",
        )
        assert family_hit, (
            "Expected lightspeed tool family (e.g. health_status) in the filtered tool "
            f"list or mock AAP. filtered={names[:20]!r} mock_paths={tool_paths!r}"
        )
        assert not any(hint in joined_names for hint in _CONTROLLER_HINTS), (
            "Controller tool family hints leaked into a lightspeed-only filter result: "
            f"filtered={names[:20]!r}"
        )

    def test_knowledge_search_always_included(
        self, base_url, mcp_provider_setup, mcp_filter_debug
    ):
        """RAG still answers product questions when MCP tools are attached."""
        output_lines = mcp_provider_setup["output_lines"]
        start = _lines_len(output_lines)
        response = _post_query(base_url, mcp_provider_setup, "What is AAP?")
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        response_data = response.json()
        response_text = _response_text(response_data)
        assert isinstance(response_text, str), (
            f"Unexpected response shape: {type(response_text)} — keys: {list(response_data.keys())}"
        )
        assert len(response_text) > 0, "Response should not be empty"
        content_lower = response_text.lower()
        assert any(
            kw in content_lower
            for kw in ["ansible automation platform", "aap", "ansible", "automation"]
        ), f"Response should mention Ansible or AAP. Got: {response_text[:200]}"

        new_lines = _lines_from(output_lines, start)
        mcp_filter_debug(new_lines, "What is AAP?")
        always_included = {n.lower() for n in _always_included_tools(new_lines)}
        assert always_included, (
            "Could not find the 'Always included tools' log line — "
            "the marker may have changed upstream."
        )
        assert "knowledge_search" in always_included, (
            f"knowledge_search should be always-included: {sorted(always_included)!r}"
        )

    def test_tool_call_error_is_handled(self, base_url, mcp_provider_setup):
        """A 404 from mock AAP must not crash the chatbot container."""
        mock_aap = mcp_provider_setup["mock_aap"]
        mock_aap.clear()
        response = _post_query(
            base_url,
            mcp_provider_setup,
            "List the job templates available in automation controller.",
        )
        assert response.status_code in (200, 400, 422), (
            f"Expected 200/400/422 after a failing tool call, got {response.status_code}: "
            f"{response.text}"
        )
        assert mock_aap.tool_requests(), (
            "no tool call reached mock AAP — the 404 path was never exercised. "
            "If the tool was filtered in but never invoked (Granite), check that: "
            "(1) vLLM was started with --enable-auto-tool-choice and a matching "
            "--tool-call-parser, and (2) the chatbot is using "
            "ansible-chatbot-system-prompt-granite-compat.txt. See README's MCP "
            "sanity tests section."
        )

        health = requests.get(f"{base_url}/v1/config", timeout=10)
        assert health.status_code == 200, (
            f"Chatbot became unhealthy after MCP tool call: {health.status_code}"
        )


@pytest.mark.mcp
class TestFilterDebugSummary:
    """Parser checks for --mcp-debug output; no containers required."""

    def test_summarizes_before_and_after_counts(self):
        lines = [
            "INFO agents: Tool filtering enabled - filtering 87 tools (threshold: 10)",
            "INFO agents: Filtered tool names from LLM: ['job_templates_list', 'knowledge_search']",
        ]
        summary = format_filter_debug(lines, "List job templates")
        assert "87 tools in → 2 kept (85 fewer)" in summary
        assert "job_templates_list" in summary
        assert "knowledge_search" in summary

    def test_summarizes_skipped_filtering(self):
        lines = [
            "INFO agents: Skipping tool filtering - 1 tools (threshold: 10)",
        ]
        summary = format_filter_debug(lines, "What is AAP?")
        assert "skipped" in summary
        assert "catalog=1" in summary
