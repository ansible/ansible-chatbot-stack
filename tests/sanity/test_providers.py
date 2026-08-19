"""
End-to-end sanity tests for ansible-chatbot-stack against real LLM providers.

Tests run for each provider configured via the provider_setup fixture:
  - Granite (vLLM)  — requires VLLM_URL, VLLM_API_TOKEN, INFERENCE_MODEL
  - OpenAI          — requires OPENAI_API_KEY, OPENAI_INFERENCE_MODEL
  - Azure OpenAI    — requires AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY,
                               AZURE_OPENAI_INFERENCE_MODEL

A provider is skipped automatically when its required variables are not set.

Examples:
    make test-sanity                  # all providers
    make test-sanity-granite          # Granite only
    make test-sanity-openai           # OpenAI only
    make test-sanity-azure            # Azure only
"""

import json

import pytest
import requests


class TestChatbotSanity:
    """Sanity tests executed once per configured LLM provider."""

    def test_server_health(self, base_url, provider_setup):
        response = requests.get(f"{base_url}/v1/config", timeout=10)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        config_data = response.json()
        assert config_data is not None
        assert len(config_data) > 0

    def test_models_endpoint(self, base_url, provider_setup):
        response = requests.get(f"{base_url}/v1/models", timeout=10)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        models_data = response.json()
        assert models_data is not None
        assert isinstance(models_data, (list, dict))

    def test_simple_query_what_is_aap(self, base_url, provider_setup):
        query_data = {
            "query": "What is AAP?",
            "model": provider_setup["model"],
            "provider": provider_setup["provider"],
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
        assert response_data is not None

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

    def test_streaming_query_what_is_aap(self, base_url, provider_setup):
        query_data = {
            "query": "What is AAP?",
            "model": provider_setup["model"],
            "provider": provider_setup["provider"],
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

        content_lower = full_response.lower()
        assert any(
            kw in content_lower
            for kw in ["ansible automation platform", "aap", "ansible", "automation"]
        ), f"Response should mention Ansible or AAP. Got: {full_response[:200]}"

    def test_query_with_empty_query_does_not_crash(self, base_url, provider_setup):
        query_data = {
            "query": "",
            "model": provider_setup["model"],
            "provider": provider_setup["provider"],
        }

        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )

        assert response.status_code in [200, 400, 422], (
            f"Expected 200, 400, or 422, got {response.status_code}"
        )

    def test_query_response_structure(self, base_url, provider_setup):
        query_data = {
            "query": "What is Ansible?",
            "model": provider_setup["model"],
            "provider": provider_setup["provider"],
        }

        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )

        assert response.status_code == 200

        response_data = response.json()
        assert isinstance(response_data, dict)

        expected_fields = ["response", "answer", "text", "result", "message"]
        has_response_field = any(f in response_data for f in expected_fields)
        assert has_response_field, (
            f"Response should have one of {expected_fields}. Got: {list(response_data.keys())}"
        )
