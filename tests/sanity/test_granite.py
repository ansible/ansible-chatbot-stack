"""
Sanity tests for ansible-chatbot-stack with the in-house Granite LLM (vLLM).

These tests use a real vLLM inference endpoint. Required environment variables:
  VLLM_URL            - Base URL of the vLLM server
  VLLM_API_TOKEN      - API token for the vLLM server
  INFERENCE_MODEL     - Model name served by vLLM (e.g. ibm-granite/granite-3.3-8b-instruct)

Tests are automatically skipped when any of the above variables are not set.

Examples:
    make test-sanity-granite
    pytest tests/sanity/test_granite.py -v
"""

import json

import pytest
import requests


@pytest.mark.granite
@pytest.mark.usefixtures("granite_server")
class TestChatbotSanityGranite:
    """Sanity tests for the Granite (vLLM) inference provider."""

    def test_server_health(self, base_url):
        response = requests.get(f"{base_url}/v1/config", timeout=10)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        config_data = response.json()
        assert config_data is not None
        assert len(config_data) > 0

    def test_models_endpoint(self, base_url):
        response = requests.get(f"{base_url}/v1/models", timeout=10)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        models_data = response.json()
        assert models_data is not None
        assert isinstance(models_data, (list, dict))

    def test_simple_query_what_is_aap(self, base_url, granite_config):
        query_data = {
            "query": "What is AAP?",
            "model": granite_config["model"],
            "provider": granite_config["provider"],
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

        assert len(response_text) > 0, "Response should not be empty"
        content_lower = response_text.lower()
        assert any(
            kw in content_lower
            for kw in ["ansible automation platform", "aap", "ansible", "automation"]
        ), f"Response should mention Ansible or AAP. Got: {response_text[:200]}"

    def test_streaming_query_what_is_aap(self, base_url, granite_config):
        query_data = {
            "query": "What is AAP?",
            "model": granite_config["model"],
            "provider": granite_config["provider"],
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

        for line in response.iter_lines():
            if line:
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    chunks_received += 1
                    try:
                        chunk_data = json.loads(line_str[6:])
                        if chunk_data.get("event") == "token":
                            full_response += chunk_data.get("data", {}).get("token", "")
                    except json.JSONDecodeError:
                        pass

        assert chunks_received > 0, "Should receive at least one streaming chunk"
        assert len(full_response) > 0, "Streamed response should not be empty"

        content_lower = full_response.lower()
        assert any(
            kw in content_lower
            for kw in ["ansible automation platform", "aap", "ansible", "automation"]
        ), f"Response should mention Ansible or AAP. Got: {full_response[:200]}"

    def test_query_with_empty_query_returns_error(self, base_url, granite_config):
        query_data = {
            "query": "",
            "model": granite_config["model"],
            "provider": granite_config["provider"],
        }

        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )

        assert response.status_code in [200, 400, 422], (
            f"Expected 200, 400, or 422, got {response.status_code}"
        )

    def test_query_response_structure(self, base_url, granite_config):
        query_data = {
            "query": "What is Ansible?",
            "model": granite_config["model"],
            "provider": granite_config["provider"],
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
