"""
Pytest configuration and fixtures for ansible-chatbot-stack tests.

This module provides fixtures for integration testing the chatbot with a mock OpenAI server.
The mock server returns static responses, allowing tests to run without real API keys.
"""

import pytest
import os
import sys
import subprocess
import time
import requests
import threading
import json
import shutil
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler


@pytest.fixture(scope="session")
def base_url():
    """Base URL for the chatbot stack API."""
    return "http://127.0.0.1:8322"


@pytest.fixture(scope="session")
def openai_config():
    """OpenAI provider configuration for the chatbot."""
    return {
        "model": os.environ.get("OPENAI_INFERENCE_MODEL", "gpt-4o-mini"),
        "provider": "openai",
    }


class MockOpenAIHandler(BaseHTTPRequestHandler):
    """
    Mock OpenAI API server for testing.
    
    Returns static responses that simulate OpenAI API behavior.
    Supports both streaming and non-streaming responses.
    """
    
    def log_message(self, format, *args):
        """Suppress request logging."""
        pass
    
    def do_POST(self):
        """Handle POST requests to OpenAI API."""
        if self.path == "/v1/chat/completions":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            request_body = json.loads(post_data.decode('utf-8'))
            
            # Check if streaming is requested
            is_streaming = request_body.get('stream', False)
            
            if is_streaming:
                self._handle_streaming_response()
            else:
                self._handle_non_streaming_response()
        else:
            self.send_response(404)
            self.end_headers()
    
    def _handle_streaming_response(self):
        """Return a streaming SSE response."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        
        # Simulate streaming tokens
        tokens = [
            "AAP", " stands", " for", " Ansible", " Automation", " Platform", ".", 
            " It", " is", " a", " comprehensive", " enterprise", " automation", " solution",
            " by", " Red", " Hat", " for", " IT", " automation", " and", " orchestration", "."
        ]
        
        for token in tokens:
            chunk = {
                "id": "chatcmpl-test-stream",
                "object": "chat.completion.chunk",
                "created": 1732800000,
                "model": "gpt-4o-mini",
                "choices": [{
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None
                }]
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        
        # Send final chunk
        final_chunk = {
            "id": "chatcmpl-test-stream",
            "object": "chat.completion.chunk",
            "created": 1732800000,
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        self.wfile.write(f"data: {json.dumps(final_chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
    
    def _handle_non_streaming_response(self):
        """Return a non-streaming JSON response."""
        response = {
            "id": "chatcmpl-test-123",
            "object": "chat.completion",
            "created": 1732800000,
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        "AAP stands for Ansible Automation Platform. It is a comprehensive "
                        "enterprise solution developed by Red Hat that provides powerful "
                        "automation capabilities for IT operations, cloud provisioning, "
                        "configuration management, application deployment, and orchestration.\n\n"
                        "Key components include Automation Controller, Automation Hub, "
                        "Automation Execution Environments, Ansible Content Collections, "
                        "and Event-Driven Ansible. AAP helps organizations automate complex "
                        "workflows, improve operational efficiency, and ensure consistency "
                        "across environments."
                    )
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 28,
                "completion_tokens": 98,
                "total_tokens": 126
            }
        }
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())


@pytest.fixture(scope="session")
def mock_openai_server():
    """
    Start a mock OpenAI API server for testing.
    
    The server runs on port 8323 and returns static responses
    that simulate OpenAI API behavior.
    """
    print("\n[Starting mock OpenAI API server on port 8323]")
    server = HTTPServer(('127.0.0.1', 8323), MockOpenAIHandler)
    
    # Run server in background thread
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    # Wait for server to be ready
    time.sleep(1)
    
    yield server
    
    # Cleanup
    print("\n[Stopping mock OpenAI API server]")
    server.shutdown()


@pytest.fixture(scope="session")
def chatbot_server(mock_openai_server):
    """
    Auto-start the chatbot server for testing.
    
    The server starts with:
    - Mock OpenAI server (no real API calls needed)
    - All chatbot dependencies (llama-stack, vector DB, agents, etc.)
    """
    base_url = "http://127.0.0.1:8322"
    
    print(f"\n[Checking for running chatbot server at {base_url}]")
    
    # Check if server is already running
    try:
        response = requests.get(f"{base_url}/v1/config", timeout=2)
        if response.status_code == 200:
            print("[✓] Chatbot server already running")
            yield True
            return
    except requests.exceptions.RequestException:
        pass
    
    # Server not running - start it
    print("[⚙] Starting chatbot server...")
    
    # Set up environment
    env = os.environ.copy()
    
    # Point to mock OpenAI server (using host network)
    mock_openai_url = "http://127.0.0.1:8323/v1"
    env.update({
        "OPENAI_API_KEY": "fake-test-key",
        "OPENAI_BASE_URL": mock_openai_url,
        "OPENAI_INFERENCE_MODEL": "gpt-4o-mini",
    })
    print(f"[✓] OPENAI_BASE_URL: {mock_openai_url} (mock server)")
    
    # Read PROVIDER_VECTOR_DB_ID from file or use default
    provider_vector_db_id = "aap-product-docs-2_6"
    vector_db_id_file = Path("./vector_db/provider_vector_db_id.ind")
    if vector_db_id_file.exists():
        try:
            provider_vector_db_id = vector_db_id_file.read_text().strip()
            print(f"[✓] PROVIDER_VECTOR_DB_ID: {provider_vector_db_id} (from file)")
        except Exception:
            print(f"[⚠] Could not read vector DB ID file, using default: {provider_vector_db_id}")
    else:
        print(f"[⚠] vector_db/provider_vector_db_id.ind not found, using: {provider_vector_db_id}")
    
    # Common environment variables
    env.update({
        "PROVIDER_VECTOR_DB_ID": provider_vector_db_id,
        "EMBEDDINGS_MODEL": "./embeddings_model",
        "VECTOR_DB_DIR": "./vector_db",
        "PROVIDERS_DB_DIR": "./test_data",
        "PYTHONUNBUFFERED": "1",
        "LOG_LEVEL": env.get("LOG_LEVEL", "INFO"),
    })
    
    # Create test_data directory if it doesn't exist
    Path("./test_data").mkdir(exist_ok=True)
    
    # Check prerequisites
    if not Path("./embeddings_model").exists():
        pytest.fail("embeddings_model directory not found - run 'make setup-test' first")
    if not Path("./vector_db/aap_faiss_store.db").exists():
        pytest.fail("vector_db/aap_faiss_store.db not found - run 'make setup-test' first")
    
    # Check which container runtime to use
    container_runtime = os.environ.get("CONTAINER_RUNTIME")
    if container_runtime:
        container_runtime = shutil.which(container_runtime)
    if not container_runtime:
        container_runtime = shutil.which("podman") or shutil.which("docker")
    container_name = f"ansible-chatbot-test-{os.getpid()}"
    
    if not container_runtime:
        sys.stderr.write("\n[✗] Neither podman nor docker found\n")
        pytest.fail("Container runtime (podman/docker) not found")
    
    print(f"[✓] Using container runtime: {container_runtime}")
    
    # Check if container image exists
    image_name = "ansible-chatbot-stack"
    image_tag = env.get("ANSIBLE_CHATBOT_VERSION", "latest")
    full_image = f"{image_name}:{image_tag}"
    
    print(f"[⚙] Checking for container image: {full_image}")
    
    check_image = subprocess.run(
        [container_runtime, "image", "inspect", full_image],
        capture_output=True
    )
    
    if check_image.returncode != 0:
        print(f"[⚠] Container image '{full_image}' not found")
        print("[⚙] Building container image with 'make build'...")
        
        build_process = subprocess.run(
            ["make", "build"],
            env=env,
            capture_output=False,
        )
        
        if build_process.returncode != 0:
            sys.stderr.write("\n[✗] Failed to build container image\n")
            pytest.fail("Failed to build container image")
        
        print("[✓] Container image built successfully")
    else:
        print("[✓] Container image found")
    
    # Prepare container run command
    # Add :z suffix for SELinux systems (Fedora/RHEL)
    selinux_flag = ":z"
    
    cmd = [
        container_runtime, "run",
        "--rm",
        "--name", container_name,
        "--platform", "linux/amd64",
        "--security-opt", "label=disable",
        "--network", "host",  # Use host network for mock OpenAI access
        "-v", f"{Path.cwd()}/embeddings_model:/.llama/data/embeddings_model{selinux_flag}",
        "-v", f"{Path.cwd()}/vector_db/aap_faiss_store.db:/.llama/data/distributions/ansible-chatbot/aap_faiss_store.db{selinux_flag}",
        "-v", f"{Path.cwd()}/tests/test-lightspeed-stack.yaml:/.llama/distributions/ansible-chatbot/config/lightspeed-stack.yaml{selinux_flag}",
        "-v", f"{Path.cwd()}/tests/test-ansible-chatbot-run.yaml:/.llama/distributions/llama-stack/config/ansible-chatbot-run.yaml{selinux_flag}",
        "-v", f"{Path.cwd()}/ansible-chatbot-system-prompt.txt:/.llama/distributions/ansible-chatbot/system-prompts/default.txt{selinux_flag}",
        "-v", f"{Path.cwd()}/llama-stack/providers.d:/.llama/providers.d{selinux_flag}",
        "--env", f"OPENAI_API_KEY={env['OPENAI_API_KEY']}",
        "--env", f"OPENAI_BASE_URL={env['OPENAI_BASE_URL']}",
        "--env", f"OPENAI_INFERENCE_MODEL={env['OPENAI_INFERENCE_MODEL']}",
        "--env", f"PROVIDER_VECTOR_DB_ID={env['PROVIDER_VECTOR_DB_ID']}",
        "--env", "PYTHONUNBUFFERED=1",
        "--env", f"LOG_LEVEL={env['LOG_LEVEL']}",
        full_image
    ]
    
    print("[✓] Container command prepared")
    
    # Start the server process
    process = None
    output_lines = []
    output_lock = threading.Lock()
    
    def capture_output(pipe, prefix=""):
        """Capture and display server output in real-time."""
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    line_stripped = line.rstrip()
                    with output_lock:
                        output_lines.append(line_stripped)
                        print(f"{prefix}{line_stripped}")
        except Exception as e:
            print(f"[⚠] Error capturing output: {e}")
    
    try:
        print(f"[⚙] Starting server...")
        print("=" * 80)
        
        process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        
        # Start threads to capture stdout and stderr
        stdout_thread = threading.Thread(
            target=capture_output, 
            args=(process.stdout, "[STDOUT] "),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=capture_output,
            args=(process.stderr, "[STDERR] "),
            daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        
        # Wait for server to be ready
        max_wait = int(os.environ.get("SERVER_STARTUP_TIMEOUT", "300"))
        server_ready = False
        
        print("=" * 80)
        print(f"[⏳] Waiting for server (max {max_wait}s)...")
        
        for i in range(max_wait):
            # Check if process died
            if process.poll() is not None:
                exit_code = process.poll()
                sys.stderr.write(f"\n[✗] SERVER DIED (exit code: {exit_code})\n")
                
                with output_lock:
                    recent_output = output_lines[-30:] if len(output_lines) > 30 else output_lines
                    if recent_output:
                        sys.stderr.write("\n[Last output:]\n")
                        for line in recent_output:
                            sys.stderr.write(line + "\n")
                
                sys.stderr.flush()
                pytest.fail(f"Server process exited unexpectedly with code {exit_code}")
            
            try:
                response = requests.get(f"{base_url}/v1/config", timeout=2)
                if response.status_code == 200:
                    print(f"[✓] Server ready ({i+1}s)")
                    server_ready = True
                    break
            except requests.exceptions.RequestException:
                if (i + 1) % 30 == 0:
                    print(f"[⏳] Waiting... ({i+1}s)")
                time.sleep(1)
                
        if not server_ready:
            sys.stderr.write(f"\n[✗] SERVER TIMEOUT after {max_wait}s\n")
            
            with output_lock:
                recent_output = output_lines[-30:] if len(output_lines) > 30 else output_lines
                if recent_output:
                    sys.stderr.write("\n[Last output:]\n")
                    for line in recent_output:
                        sys.stderr.write(line + "\n")
            
            sys.stderr.flush()
            
            if process:
                process.kill()
            
            pytest.fail(f"Chatbot server failed to start within {max_wait} seconds")
        
        yield process
        
    except Exception as e:
        print(f"\n[✗] Failed to start server: {e}")
        if process:
            process.kill()
        pytest.fail(f"Could not start chatbot server: {e}")
        
    finally:
        # Cleanup - stop container
        if process and process.poll() is None:
            print("\n[⏹] Stopping chatbot container...")
            try:
                process.terminate()
                process.wait(timeout=10)
                print("[✓] Container stopped")
            except subprocess.TimeoutExpired:
                process.kill()
                print("[✓] Container killed")
            except Exception as e:
                print(f"[⚠] Error stopping container: {e}")
        
        # Ensure container is removed
        if container_runtime:
            try:
                subprocess.run(
                    [container_runtime, "stop", container_name],
                    capture_output=True,
                    timeout=10
                )
                subprocess.run(
                    [container_runtime, "rm", "-f", container_name],
                    capture_output=True,
                    timeout=5
                )
            except Exception:
                pass
