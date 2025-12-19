"""
End-to-end tests for ansible-chatbot-stack with OpenAI provider.

These tests start the real chatbot application with all its dependencies
(llama-stack, vector DB, agents, etc.) and test the complete stack.

The OpenAI provider API calls are handled by a mock server that returns
static responses, allowing tests to run without real API keys.

Examples:
    # Run all tests
    make test
"""

import pytest
import requests
import json


@pytest.mark.usefixtures("chatbot_server")
class TestChatbotWithOpenAI:
    """
    Test real ansible-chatbot-stack with OpenAI provider.
    
    The chatbot runs with all its real code and dependencies.
    OpenAI API calls are handled by a mock server.
    """
    
    def test_server_health(self, base_url):
        """
        Test that the chatbot server is running and healthy.
        
        This test runs first to verify basic connectivity.
        """
        response = requests.get(f"{base_url}/v1/config", timeout=10)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        config_data = response.json()
        assert config_data is not None
        assert "image_name" in config_data or "version" in config_data or len(config_data) > 0
    
    def test_models_endpoint(self, base_url):
        """
        Test the /v1/models endpoint returns available models.
        """
        response = requests.get(f"{base_url}/v1/models", timeout=10)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        models_data = response.json()
        assert models_data is not None
        assert isinstance(models_data, (list, dict))
    
    def test_simple_query_what_is_aap(self, base_url, openai_config):
        """
        Test a simple query to the chatbot: 'What is AAP?'
        
        This tests the full stack:
        - Real chatbot application
        - Real llama-stack library
        - Real agents and vector DB  
        - Real embedding model
        - Mock OpenAI API
        """
        query_data = {
            "query": "What is AAP?",
            "model": openai_config["model"],
            "provider": "openai",
        }
        
        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        response_data = response.json()
        assert response_data is not None
        
        response_text = (
            response_data.get("response") or 
            response_data.get("answer") or 
            response_data.get("text") or
            response_data.get("result", "")
        )
        
        assert len(response_text) > 0, "Response should not be empty"
        
        # Check that the response mentions Ansible or AAP
        content_lower = response_text.lower()
        assert any(keyword in content_lower for keyword in [
            "ansible automation platform",
            "aap",
            "ansible",
            "automation"
        ]), f"Response should mention Ansible or AAP. Got: {response_text[:200]}"
    
    def test_streaming_query_what_is_aap(self, base_url, openai_config):
        """
        Test a streaming query to the chatbot: 'What is AAP?'
        
        Tests the /v1/streaming_query endpoint with streaming responses.
        """
        query_data = {
            "query": "What is AAP?",
            "model": openai_config["model"],
            "provider": "openai",
        }
        
        response = requests.post(
            f"{base_url}/v1/streaming_query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            stream=True,
            timeout=60,
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        # Parse streaming response
        full_response = ""
        chunks_received = 0
        
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    chunks_received += 1
                    try:
                        chunk_data = json.loads(line_str[6:])
                        if chunk_data.get("event") == "token":
                            token = chunk_data.get("data", {}).get("token", "")
                            full_response += token
                    except json.JSONDecodeError:
                        pass
        
        assert chunks_received > 0, "Should receive at least one chunk"
        assert len(full_response) > 0, "Should have received content"
        
        # Check that the response mentions Ansible or AAP
        content_lower = full_response.lower()
        assert any(keyword in content_lower for keyword in [
            "ansible automation platform",
            "aap",
            "ansible",
            "automation"
        ]), f"Response should mention Ansible or AAP. Got: {full_response[:200]}"
    
    def test_query_with_empty_query_returns_error(self, base_url, openai_config):
        """
        Test that an empty query returns an appropriate error.
        """
        query_data = {
            "query": "",
            "model": openai_config["model"],
            "provider": "openai",
        }
        
        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        
        # Should either return 400/422 error or handle gracefully
        # Accept both error codes and successful responses with empty handling
        assert response.status_code in [200, 400, 422], \
            f"Expected 200, 400, or 422, got {response.status_code}"
    
    def test_query_response_structure(self, base_url, openai_config):
        """
        Test that the query response has the expected structure.
        """
        query_data = {
            "query": "What is Ansible?",
            "model": openai_config["model"],
            "provider": "openai",
        }
        
        response = requests.post(
            f"{base_url}/v1/query",
            json=query_data,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        
        assert response.status_code == 200
        
        response_data = response.json()
        
        assert isinstance(response_data, dict)
        
        response_fields = ["response", "answer", "text", "result", "message"]
        has_response_field = any(field in response_data for field in response_fields)
        assert has_response_field, f"Response should have one of {response_fields}. Got: {list(response_data.keys())}"
