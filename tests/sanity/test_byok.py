"""
BYOK (Bring Your Own Knowledge) sanity tests for ansible-chatbot-stack.

Tests run for each provider configured via the byok_provider_setup fixture:
  - Granite (vLLM)  — requires VLLM_URL, VLLM_API_TOKEN, INFERENCE_MODEL
  - OpenAI          — requires OPENAI_API_KEY, OPENAI_INFERENCE_MODEL
  - Azure OpenAI    — requires AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY,
                               AZURE_OPENAI_INFERENCE_MODEL
  - Vertex AI       — requires VERTEX_AI_CREDENTIALS, VERTEX_AI_PROJECT

A provider is skipped automatically when its required variables are not set.
The BYOK vector DB must exist at .test_data/byok_vector_db/ (created by 'make setup-sanity-test-data').

Examples:
    make test-sanity-byok                 # all providers, BYOK mode
    pytest tests/sanity/ -v -m byok       # same
    pytest tests/sanity/ -v -m "byok and granite"  # Granite only
"""

import json

import pytest
import requests


class TestBYOKSanity:
    """Sanity tests for BYOK vector DB retrieval alongside the standard AAP RAG."""

    def test_server_health(self, base_url, byok_provider_setup):
        response = requests.get(f"{base_url}/v1/config", timeout=10)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        config_data = response.json()
        assert config_data is not None
        assert len(config_data) > 0
        # /v1/config nests fields under a top-level "configuration" key (config_data
        # itself only has that one key), so search the whole dump rather than assuming
        # a specific nesting level — matches the equivalent check in test_mcp.py.
        config_text = json.dumps(config_data).lower()
        assert "byok_rag" in config_text or "byok" in config_text, (
            f"BYOK config (byok_rag) not found in /v1/config response: {config_text[:2000]}"
        )

    def test_base_vector_db_retrieval(self, base_url, byok_provider_setup):
        """Standard AAP RAG is still active when BYOK is enabled."""
        query_data = {
            "query": "What is AAP?",
            "model": byok_provider_setup["model"],
            "provider": byok_provider_setup["provider"],
        }

        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        response_data = response.json()
        response_text = (
            response_data.get("response")
            or response_data.get("answer")
            or response_data.get("text")
            or response_data.get("result", "")
        )

        assert isinstance(response_text, str), (
            f"Unexpected response shape: {type(response_text)} — keys: {list(response_data.keys())}"
        )
        assert len(response_text) > 0, "Response should not be empty"
        content_lower = response_text.lower()
        assert any(
            kw in content_lower
            for kw in ["ansible automation platform", "aap", "ansible", "automation"]
        ), f"Response should mention Ansible or AAP. Got: {response_text[:200]}"

    def test_byok_vector_db_retrieval(self, base_url, byok_provider_setup):
        """BYOK vector DB content is retrieved when queried."""
        query_data = {
            "query": "What is AnsibleByokPlugin?",
            "model": byok_provider_setup["model"],
            "provider": byok_provider_setup["provider"],
        }

        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        response_data = response.json()
        response_text = (
            response_data.get("response")
            or response_data.get("answer")
            or response_data.get("text")
            or response_data.get("result", "")
        )

        assert isinstance(response_text, str), (
            f"Unexpected response shape: {type(response_text)} — keys: {list(response_data.keys())}"
        )
        assert len(response_text) > 0, "Response should not be empty"
        content_lower = response_text.lower()
        # These phrases only exist in the BYOK corpus fixture (tests/setup_test_data.py) —
        # unlike "byok"/"plugin", they can't be produced by echoing the question or by a
        # generic "I don't have information" answer, so a match proves BYOK retrieval fired.
        assert any(
            kw in content_lower
            for kw in [
                "real-time event processing",
                "version 1.0",
                "integrates custom knowledge sources",
                "dynamic knowledge retrieval",
                "fictional automation plugin",
            ]
        ), (
            f"Response should reference distinctive BYOK corpus content. Got: {response_text[:200]}"
        )

    def test_streaming_with_byok(self, base_url, byok_provider_setup):
        """Streaming queries work correctly with BYOK enabled."""
        query_data = {
            "query": "What is AAP?",
            "model": byok_provider_setup["model"],
            "provider": byok_provider_setup["provider"],
        }

        response = requests.post(
            f"{base_url}/v1/streaming_query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            stream=True,
            timeout=120,
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        full_response = ""
        chunks_received = 0
        non_token_events = []

        for line in response.iter_lines():
            if line:
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    chunks_received += 1
                    try:
                        chunk_data = json.loads(line_str[6:])
                        if chunk_data.get("event") == "token":
                            full_response += chunk_data.get("data", {}).get("token", "")
                        else:
                            non_token_events.append(chunk_data)
                    except (json.JSONDecodeError, AttributeError):
                        pass

        assert chunks_received > 0, "Should receive at least one streaming chunk"
        assert len(full_response) > 0, (
            f"Streamed response should not be empty. "
            f"Non-token events received: {non_token_events}"
        )
