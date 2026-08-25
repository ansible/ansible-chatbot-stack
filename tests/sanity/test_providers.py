"""
End-to-end sanity tests for ansible-chatbot-stack against real LLM providers.

Tests run for each provider configured via the provider_setup fixture:
  - Granite (vLLM)  — requires VLLM_URL, VLLM_API_TOKEN, INFERENCE_MODEL
  - OpenAI          — requires OPENAI_API_KEY, OPENAI_INFERENCE_MODEL
  - Azure OpenAI    — requires AZURE_OPENAI_BASE_URL, AZURE_OPENAI_API_KEY,
                               AZURE_OPENAI_INFERENCE_MODEL
  - Vertex AI       — requires VERTEX_AI_CREDENTIALS, VERTEX_AI_PROJECT
                      (default model google/gemini-2.5-pro)

A provider is skipped automatically when its required variables are not set.

Examples:
    make test-sanity                  # all providers
    make test-sanity-granite          # Granite only
    make test-sanity-openai           # OpenAI only
    make test-sanity-azure            # Azure only
    make test-sanity-vertexai         # Vertex AI only
"""

import json
import os
from pathlib import Path

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


def test_vertexai_run_config():
    """Vertex AI sanity run configs must declare the provider, ADC project/location, and default model."""
    from tests.sanity.conftest import _VERTEXAI_DEFAULT_MODEL

    sanity_dir = Path(__file__).parent
    for name in ("vertexai-chatbot-run.yaml", "mcp-vertexai-chatbot-run.yaml"):
        text = (sanity_dir / name).read_text()
        assert "provider_id: vertexai" in text, name
        assert "provider_type: remote::vertexai" in text, name
        assert "project: ${env.VERTEX_AI_PROJECT:=}" in text, name
        assert "location: ${env.VERTEX_AI_LOCATION:=us-central1}" in text, name
        assert _VERTEXAI_DEFAULT_MODEL in text, name


def test_vertexai_provider_config_skipped_without_credentials(monkeypatch):
    monkeypatch.delenv("VERTEX_AI_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("VERTEX_AI_PROJECT", raising=False)
    from tests.sanity.conftest import _build_provider_config

    with pytest.raises(pytest.skip.Exception, match="VERTEX_AI_CREDENTIALS"):
        _build_provider_config("vertexai")


def test_vertexai_provider_config_defaults(monkeypatch):
    payload = '{"type":"service_account","project_id":"sanity-vertex-project"}'
    monkeypatch.setenv("VERTEX_AI_CREDENTIALS", payload)
    monkeypatch.setenv("VERTEX_AI_PROJECT", "sanity-vertex-project")
    monkeypatch.delenv("VERTEX_AI_INFERENCE_MODEL", raising=False)
    monkeypatch.delenv("VERTEX_AI_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    from tests.sanity.conftest import (
        _GOOGLE_ADC_CONTAINER_PATH,
        _VERTEXAI_DEFAULT_MODEL,
        _build_provider_config,
        _cleanup_vertex_adc_file,
        _google_adc_volume_mount,
    )

    run_config, env_overrides, config = _build_provider_config("vertexai")
    creds_path = config.pop("credentials_file")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", creds_path)
    try:
        assert run_config.endswith("vertexai-chatbot-run.yaml")
        assert config == {"model": _VERTEXAI_DEFAULT_MODEL, "provider": "vertexai"}
        assert Path(creds_path).is_file()
        assert Path(creds_path).read_text() == payload
        assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == creds_path
        assert env_overrides["GOOGLE_APPLICATION_CREDENTIALS"] == creds_path
        assert env_overrides["VERTEX_AI_PROJECT"] == "sanity-vertex-project"
        assert env_overrides["VERTEX_AI_INFERENCE_MODEL"] == _VERTEXAI_DEFAULT_MODEL
        assert "VERTEX_AI_LOCATION" not in env_overrides
        assert "VERTEX_AI_CREDENTIALS" not in env_overrides

        mount = _google_adc_volume_mount(env_overrides)
        assert mount is not None
        assert mount.startswith(f"{Path(creds_path).resolve()}:")
        assert mount.endswith(f"{_GOOGLE_ADC_CONTAINER_PATH}:ro,z")
        assert env_overrides["GOOGLE_APPLICATION_CREDENTIALS"] == _GOOGLE_ADC_CONTAINER_PATH
    finally:
        _cleanup_vertex_adc_file(creds_path)
    assert not Path(creds_path).exists()
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ

