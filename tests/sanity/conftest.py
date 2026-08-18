"""
Pytest fixtures for sanity tests against real LLM providers.

Each provider fixture (granite_server, openai_real_server, azure_server) manages
the full container lifecycle. Tests are skipped automatically when the required
environment variables for a provider are not set.
"""

import os
import sys
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
import requests


BASE_URL = "http://127.0.0.1:8322"

_GRANITE_REQUIRED = ["VLLM_URL", "VLLM_API_TOKEN", "INFERENCE_MODEL"]
_OPENAI_REQUIRED = ["OPENAI_API_KEY", "OPENAI_INFERENCE_MODEL"]
_AZURE_REQUIRED = ["AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_INFERENCE_MODEL"]

_SANITY_DIR = Path(__file__).parent
_LIGHTSPEED_STACK_CONFIG = str(_SANITY_DIR / "lightspeed-stack.yaml")


def _check_required_vars(var_names):
    """Skip the test session if any required environment variable is missing."""
    missing = [v for v in var_names if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Missing required environment variables: {', '.join(missing)}")


def _start_sanity_server(run_config_path, lightspeed_config_path, env_overrides):
    """
    Start the chatbot container for a given provider config.

    Returns (process, container_runtime, container_name) for cleanup.
    If a server is already running on port 8322, skips container startup.
    """
    env = os.environ.copy()
    env.update(env_overrides)

    # Check prerequisites
    if not Path("./embeddings_model").exists():
        pytest.fail("embeddings_model directory not found — run 'make setup-test' first")
    if not Path("./vector_db/aap_faiss_store.db").exists():
        pytest.fail("vector_db/aap_faiss_store.db not found — run 'make setup-test' first")

    # Check if server is already running
    try:
        resp = requests.get(f"{BASE_URL}/v1/config", timeout=2)
        if resp.status_code == 200:
            print(f"\n[✓] Chatbot server already running at {BASE_URL}")
            return None, None, None
    except requests.exceptions.RequestException:
        pass

    # Resolve container runtime — allowlist prevents arbitrary command injection
    _ALLOWED_RUNTIMES = ("podman", "docker")
    requested = os.environ.get("CONTAINER_RUNTIME", "")
    if requested and requested not in _ALLOWED_RUNTIMES:
        pytest.fail(f"CONTAINER_RUNTIME must be one of {_ALLOWED_RUNTIMES}, got: {requested!r}")
    container_runtime = shutil.which(requested) if requested else None
    if not container_runtime:
        container_runtime = shutil.which("podman") or shutil.which("docker")
    if not container_runtime:
        pytest.fail("Container runtime (podman/docker) not found")

    print(f"\n[✓] Using container runtime: {container_runtime}")

    # Resolve container image
    image_tag = env.get("ANSIBLE_CHATBOT_VERSION", "latest")
    full_image = f"ansible-chatbot-stack:{image_tag}"

    check_image = subprocess.run(
        [container_runtime, "image", "inspect", full_image],
        capture_output=True,
    )
    if check_image.returncode != 0:
        print(f"[⚙] Image '{full_image}' not found — building with 'make build'...")
        build = subprocess.run(["make", "build"], env=env, capture_output=False)
        if build.returncode != 0:
            pytest.fail("Failed to build container image")

    container_name = f"ansible-chatbot-sanity-{os.getpid()}"
    selinux_flag = ":z"

    provider_vector_db_id = env.get("PROVIDER_VECTOR_DB_ID", "aap-product-docs-2_6")
    vid_file = Path("./vector_db/provider_vector_db_id.ind")
    if vid_file.exists() and not os.environ.get("PROVIDER_VECTOR_DB_ID"):
        try:
            provider_vector_db_id = vid_file.read_text().strip()
        except Exception:
            pass

    cmd = [
        container_runtime, "run",
        "--rm",
        "--name", container_name,
        "--platform", "linux/amd64",
        "--security-opt", "label=disable",
        "--network", "host",
        "-v", f"{Path.cwd()}/embeddings_model:/.llama/data/embeddings_model{selinux_flag}",
        "-v", f"{Path.cwd()}/vector_db/aap_faiss_store.db:/.llama/data/distributions/ansible-chatbot/aap_faiss_store.db{selinux_flag}",
        "-v", f"{lightspeed_config_path}:/.llama/distributions/ansible-chatbot/config/lightspeed-stack.yaml{selinux_flag}",
        "-v", f"{run_config_path}:/.llama/distributions/llama-stack/config/ansible-chatbot-run.yaml{selinux_flag}",
        "-v", f"{Path.cwd()}/ansible-chatbot-system-prompt.txt:/.llama/distributions/ansible-chatbot/system-prompts/default.txt{selinux_flag}",
        "-v", f"{Path.cwd()}/llama-stack/providers.d:/.llama/providers.d{selinux_flag}",
        "--env", f"PROVIDER_VECTOR_DB_ID={provider_vector_db_id}",
        "--env", "PYTHONUNBUFFERED=1",
        "--env", f"LOG_LEVEL={env.get('LOG_LEVEL', 'INFO')}",
    ]

    # Pass provider-specific env vars into the container
    for key, val in env_overrides.items():
        cmd += ["--env", f"{key}={val}"]

    cmd.append(full_image)

    output_lines = []
    output_lock = threading.Lock()

    def _capture(pipe, prefix=""):
        try:
            for line in iter(pipe.readline, ""):
                if line:
                    stripped = line.rstrip()
                    with output_lock:
                        output_lines.append(stripped)
                        print(f"{prefix}{stripped}")
        except Exception as exc:
            print(f"[⚠] Output capture error: {exc}")

    print(f"[⚙] Starting container: {container_name}")
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    threading.Thread(target=_capture, args=(process.stdout, "[STDOUT] "), daemon=True).start()
    threading.Thread(target=_capture, args=(process.stderr, "[STDERR] "), daemon=True).start()

    max_wait = int(os.environ.get("SERVER_STARTUP_TIMEOUT", "300"))
    print(f"[⏳] Waiting for server to be ready (max {max_wait}s)...")

    for i in range(max_wait):
        if process.poll() is not None:
            with output_lock:
                recent = output_lines[-30:] if len(output_lines) > 30 else output_lines
            sys.stderr.write(f"\n[✗] Container exited (code {process.poll()})\n")
            for line in recent:
                sys.stderr.write(line + "\n")
            pytest.fail(f"Server process exited unexpectedly (code {process.poll()})")

        try:
            resp = requests.get(f"{BASE_URL}/v1/config", timeout=2)
            if resp.status_code == 200:
                print(f"[✓] Server ready ({i + 1}s)")
                return process, container_runtime, container_name
        except requests.exceptions.RequestException:
            if (i + 1) % 30 == 0:
                print(f"[⏳] Still waiting... ({i + 1}s)")
            time.sleep(1)

    with output_lock:
        recent = output_lines[-30:] if len(output_lines) > 30 else output_lines
    sys.stderr.write(f"\n[✗] Server did not start within {max_wait}s\n")
    for line in recent:
        sys.stderr.write(line + "\n")
    process.kill()
    pytest.fail(f"Chatbot server failed to start within {max_wait} seconds")


def _stop_sanity_server(process, container_runtime, container_name):
    """Terminate the container and ensure it is removed."""
    if process and process.poll() is None:
        print("\n[⏹] Stopping chatbot container...")
        try:
            process.terminate()
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    if container_runtime and container_name:
        subprocess.run([container_runtime, "stop", container_name], capture_output=True, timeout=10)
        subprocess.run([container_runtime, "rm", "-f", container_name], capture_output=True, timeout=5)


# ---------------------------------------------------------------------------
# Provider fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="module")
def granite_server():
    _check_required_vars(_GRANITE_REQUIRED)

    env_overrides = {
        "VLLM_URL": os.environ["VLLM_URL"],
        "VLLM_API_TOKEN": os.environ["VLLM_API_TOKEN"],
        "INFERENCE_MODEL": os.environ["INFERENCE_MODEL"],
    }
    if os.environ.get("VLLM_MAX_TOKENS"):
        env_overrides["VLLM_MAX_TOKENS"] = os.environ["VLLM_MAX_TOKENS"]
    if os.environ.get("VLLM_TLS_VERIFY"):
        env_overrides["VLLM_TLS_VERIFY"] = os.environ["VLLM_TLS_VERIFY"]

    run_config = str(_SANITY_DIR / "granite-chatbot-run.yaml")
    ls_config = _LIGHTSPEED_STACK_CONFIG

    process, runtime, name = _start_sanity_server(run_config, ls_config, env_overrides)
    yield True
    _stop_sanity_server(process, runtime, name)


@pytest.fixture(scope="session")
def granite_config():
    model = os.environ.get("INFERENCE_MODEL", "")
    return {"model": f"granite/{model}", "provider": "granite"}


@pytest.fixture(scope="module")
def openai_real_server():
    _check_required_vars(_OPENAI_REQUIRED)

    env_overrides = {
        "OPENAI_API_KEY": os.environ["OPENAI_API_KEY"],
        "OPENAI_INFERENCE_MODEL": os.environ["OPENAI_INFERENCE_MODEL"],
    }
    if os.environ.get("OPENAI_BASE_URL"):
        env_overrides["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]

    run_config = str(_SANITY_DIR / "openai-chatbot-run.yaml")
    ls_config = _LIGHTSPEED_STACK_CONFIG

    process, runtime, name = _start_sanity_server(run_config, ls_config, env_overrides)
    yield True
    _stop_sanity_server(process, runtime, name)


@pytest.fixture(scope="session")
def openai_real_config():
    return {
        "model": os.environ.get("OPENAI_INFERENCE_MODEL", "gpt-4o-mini"),
        "provider": "openai",
    }


@pytest.fixture(scope="module")
def azure_server():
    _check_required_vars(_AZURE_REQUIRED)

    env_overrides = {
        "AZURE_OPENAI_BASE_URL": os.environ["AZURE_OPENAI_BASE_URL"],
        "AZURE_OPENAI_API_KEY": os.environ["AZURE_OPENAI_API_KEY"],
        "AZURE_OPENAI_INFERENCE_MODEL": os.environ["AZURE_OPENAI_INFERENCE_MODEL"],
    }

    run_config = str(_SANITY_DIR / "azure-chatbot-run.yaml")
    ls_config = _LIGHTSPEED_STACK_CONFIG

    process, runtime, name = _start_sanity_server(run_config, ls_config, env_overrides)
    yield True
    _stop_sanity_server(process, runtime, name)


@pytest.fixture(scope="session")
def azure_config():
    return {
        "model": os.environ.get("AZURE_OPENAI_INFERENCE_MODEL", ""),
        "provider": "openai_azure",
    }
