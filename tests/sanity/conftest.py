"""
Pytest fixtures for sanity tests against real LLM providers.

The parametrized provider_setup fixture manages the full container lifecycle
for each provider and yields the provider config (model, provider ID).
Tests are skipped automatically when required environment variables are not set.
"""

import os
import re
import sys
import shutil
import subprocess
import tempfile
import threading
import time
import warnings
from pathlib import Path

import pytest
import requests


BASE_URL = "http://127.0.0.1:8322"

_GRANITE_REQUIRED = ["VLLM_URL", "VLLM_API_TOKEN", "INFERENCE_MODEL"]
_OPENAI_REQUIRED = ["OPENAI_API_KEY", "OPENAI_INFERENCE_MODEL"]
_AZURE_REQUIRED = ["AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_INFERENCE_MODEL"]

_SANITY_DIR = Path(__file__).parent
_LIGHTSPEED_STACK_CONFIG = str(_SANITY_DIR / "lightspeed-stack.yaml")
_BYOK_LIGHTSPEED_STACK_CONFIG = str(_SANITY_DIR / "byok-lightspeed-stack.yaml")


def _check_required_vars(var_names):
    """Skip the test session if any required environment variable is missing."""
    missing = [v for v in var_names if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Missing required environment variables: {', '.join(missing)}")


def _build_provider_config(provider):
    """Return (run_config_path, env_overrides, config_dict) for the given provider."""
    if provider == "granite":
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
        config = {"model": f"granite/{os.environ['INFERENCE_MODEL']}", "provider": "granite"}

    elif provider == "openai":
        _check_required_vars(_OPENAI_REQUIRED)
        env_overrides = {
            "OPENAI_API_KEY": os.environ["OPENAI_API_KEY"],
            "OPENAI_INFERENCE_MODEL": os.environ["OPENAI_INFERENCE_MODEL"],
        }
        if os.environ.get("OPENAI_BASE_URL"):
            env_overrides["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
        run_config = str(_SANITY_DIR / "openai-chatbot-run.yaml")
        config = {"model": os.environ["OPENAI_INFERENCE_MODEL"], "provider": "openai"}

    else:  # azure
        _check_required_vars(_AZURE_REQUIRED)
        env_overrides = {
            "AZURE_OPENAI_BASE_URL": os.environ["AZURE_OPENAI_BASE_URL"],
            "AZURE_OPENAI_API_KEY": os.environ["AZURE_OPENAI_API_KEY"],
            "AZURE_OPENAI_INFERENCE_MODEL": os.environ["AZURE_OPENAI_INFERENCE_MODEL"],
        }
        run_config = str(_SANITY_DIR / "azure-chatbot-run.yaml")
        config = {"model": os.environ["AZURE_OPENAI_INFERENCE_MODEL"], "provider": "openai_azure"}

    return run_config, env_overrides, config


def _start_sanity_server(run_config_path, lightspeed_config_path, env_overrides, provider, expected_model, byok_vector_db_path=None):  # noqa: cognitive-complexity
    """
    Start the chatbot container for a given provider config.

    Returns (process, container_runtime, container_name, env_file_path) for cleanup.
    If a server is already running on port 8322 with the expected model, skips container startup.
    When byok_vector_db_path is set, the BYOK vector DB is mounted and configured.
    """
    # Check if server is already running
    server_running = False
    try:
        resp = requests.get(f"{BASE_URL}/v1/config", timeout=2)
        server_running = resp.status_code == 200
    except requests.exceptions.RequestException:
        pass

    if server_running:
        # Validate the running server has the expected model to avoid silently
        # running tests against a stale or wrong container.
        # Let RequestException propagate naturally — unreachable /v1/models is a test failure.
        models_resp = requests.get(f"{BASE_URL}/v1/models", timeout=2)
        if models_resp.status_code != 200 or expected_model not in models_resp.text:
            pytest.fail(
                f"Stale server detected on {BASE_URL}: expected model {expected_model!r} "
                f"not found in /v1/models. Stop the existing server first."
            )
        print(f"\n[✓] Chatbot server already running at {BASE_URL}")
        return None, None, None, None

    # Check prerequisites
    if not Path("./embeddings_model").exists():
        pytest.fail("embeddings_model directory not found — run 'make setup-test' first")
    if not Path("./vector_db/aap_faiss_store.db").exists():
        pytest.fail("vector_db/aap_faiss_store.db not found — run 'make setup-test' first")
    if byok_vector_db_path and not (_SANITY_DIR.parent.parent / byok_vector_db_path / "faiss_store.db").exists():
        pytest.fail(f"{byok_vector_db_path}/faiss_store.db not found — run 'make setup-test' first")
    if not Path("./llama-stack/providers.d").exists():
        pytest.fail("llama-stack/providers.d directory not found — run 'make setup-test' first")

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

    # Resolve container image — validate tag to prevent tainted input reaching subprocess
    image_tag = os.environ.get("ANSIBLE_CHATBOT_VERSION", "latest")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._\-]*", image_tag):
        pytest.fail(f"Invalid ANSIBLE_CHATBOT_VERSION tag: {image_tag!r}")
    full_image = f"ansible-chatbot-stack:{image_tag}"

    check_image = subprocess.run(
        [container_runtime, "image", "inspect", full_image],
        capture_output=True,
    )
    if check_image.returncode != 0:
        print(f"[⚙] Image '{full_image}' not found — building with 'make build'...")
        build = subprocess.run(["make", "build"], capture_output=False)
        if build.returncode != 0:
            pytest.fail("Failed to build container image")

    # Include provider in name to avoid --rm race when running all providers in sequence
    container_name = f"ansible-chatbot-sanity-{provider}-{os.getpid()}"
    selinux_flag = ":z"

    provider_vector_db_id = os.environ.get("PROVIDER_VECTOR_DB_ID", "aap-product-docs-2_6")
    vid_file = Path("./vector_db/provider_vector_db_id.ind")
    if vid_file.exists() and not os.environ.get("PROVIDER_VECTOR_DB_ID"):
        try:
            provider_vector_db_id = vid_file.read_text().strip()
        except Exception:
            pass

    # Write provider credentials to a 0600 temp file to keep secrets off argv
    env_file = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    env_file_path = env_file.name
    try:
        for key, value in env_overrides.items():
            env_file.write(f"{key}={value}\n")
    finally:
        env_file.close()
    os.chmod(env_file_path, 0o600)

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
        "--env", f"LOG_LEVEL={os.environ.get('LOG_LEVEL', 'INFO')}",
        "--env-file", env_file_path,
    ]

    if byok_vector_db_path:
        byok_vid_file = Path(byok_vector_db_path) / "provider_vector_db_id.ind"
        byok_vector_db_id = os.environ.get("BYOK_PROVIDER_VECTOR_DB_ID", "")
        if byok_vid_file.exists() and not byok_vector_db_id:
            try:
                byok_vector_db_id = byok_vid_file.read_text().strip()
            except Exception:
                pass
        cmd += [
            "-v", f"{_SANITY_DIR.parent.parent / byok_vector_db_path}:/.llama/data/byok/distributions/ansible-chatbot{selinux_flag}",
            "--env", f"BYOK_PROVIDER_VECTOR_DB_ID={byok_vector_db_id}",
        ]

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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    threading.Thread(target=_capture, args=(process.stdout, "[STDOUT] "), daemon=True).start()
    threading.Thread(target=_capture, args=(process.stderr, "[STDERR] "), daemon=True).start()

    max_wait = int(os.environ.get("SERVER_STARTUP_TIMEOUT", "300"))
    print(f"[⏳] Waiting for server to be ready (max {max_wait}s)...")

    # Use a wall-clock deadline so the budget is measured in real seconds,
    # not iterations (each iteration can take up to 3s with a 2s request timeout).
    start = time.monotonic()
    deadline = start + max_wait
    last_progress = start
    while time.monotonic() < deadline:
        if process.poll() is not None:
            with output_lock:
                recent = output_lines[-30:] if len(output_lines) > 30 else output_lines
            sys.stderr.write(f"\n[✗] Container exited (code {process.poll()})\n")
            for line in recent:
                sys.stderr.write(line + "\n")
            try:
                os.unlink(env_file_path)
            except OSError:
                pass
            _stop_sanity_server(process, container_runtime, container_name)
            pytest.fail(f"Server process exited unexpectedly (code {process.poll()})")

        try:
            resp = requests.get(f"{BASE_URL}/v1/config", timeout=2)
            if resp.status_code == 200:
                elapsed = int(time.monotonic() - start)
                print(f"[✓] Server ready ({elapsed}s)")
                return process, container_runtime, container_name, env_file_path
        except requests.exceptions.RequestException:
            pass
        now = time.monotonic()
        if now - last_progress >= 30:
            print(f"[⏳] Still waiting... ({int(now - start)}s)")
            last_progress = now
        time.sleep(1)

    with output_lock:
        recent = output_lines[-30:] if len(output_lines) > 30 else output_lines
    sys.stderr.write(f"\n[✗] Server did not start within {max_wait}s\n")
    for line in recent:
        sys.stderr.write(line + "\n")
    try:
        os.unlink(env_file_path)
    except OSError:
        pass
    _stop_sanity_server(process, container_runtime, container_name)
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
        try:
            subprocess.run([container_runtime, "stop", container_name], capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            pass
        subprocess.run([container_runtime, "rm", "-f", container_name], capture_output=True, timeout=5)

    # Wait until port 8322 is actually closed (refused connection) before returning,
    # so the next provider's _start_sanity_server doesn't reuse a still-dying container.
    # Narrow to ConnectionError only: a timeout means the server is still alive.
    if container_name:
        deadline = time.time() + 30
        while True:
            try:
                requests.get(f"{BASE_URL}/v1/config", timeout=1)
            except requests.exceptions.Timeout:
                pass  # connect/read timeout: server still alive, keep waiting
            except requests.exceptions.ConnectionError:
                return  # port refused: actually closed
            except requests.exceptions.RequestException:
                pass  # other transient error: keep waiting
            if time.time() >= deadline:
                warnings.warn(
                    f"Container {container_name} still serving on {BASE_URL} after 30s — port may be leaked"
                )
                return
            time.sleep(1)


# ---------------------------------------------------------------------------
# Parametrized provider fixture
# ---------------------------------------------------------------------------

@pytest.fixture(
    params=[
        pytest.param("granite", marks=pytest.mark.granite),
        pytest.param("openai", marks=pytest.mark.openai_live),
        pytest.param("azure", marks=pytest.mark.azure),
    ],
    scope="module",
)
def provider_setup(request):
    """
    Start the chatbot server for the given provider and yield its config.

    Yields a dict with keys 'model' and 'provider' for use in test assertions.
    Skips automatically when required environment variables are not set.
    """
    provider = request.param
    run_config, env_overrides, config = _build_provider_config(provider)
    process, runtime, name, env_file_path = _start_sanity_server(
        run_config, _LIGHTSPEED_STACK_CONFIG, env_overrides, provider, config["model"]
    )
    try:
        yield config
    finally:
        _stop_sanity_server(process, runtime, name)
        if env_file_path:
            try:
                os.unlink(env_file_path)
            except OSError:
                pass


@pytest.fixture(
    params=[
        pytest.param("granite", marks=[pytest.mark.granite, pytest.mark.byok]),
        pytest.param("openai", marks=[pytest.mark.openai_live, pytest.mark.byok]),
        pytest.param("azure", marks=[pytest.mark.azure, pytest.mark.byok]),
    ],
    scope="module",
)
def byok_provider_setup(request):
    """
    Start the chatbot server with BYOK enabled for the given provider and yield its config.

    Uses the same inference provider env vars as provider_setup, but mounts the BYOK
    vector DB and uses byok-lightspeed-stack.yaml so both the standard AAP RAG and
    the BYOK RAG are active simultaneously.
    Skips automatically when required environment variables are not set.
    """
    provider = request.param
    run_config, env_overrides, config = _build_provider_config(provider)
    process, runtime, name, env_file_path = _start_sanity_server(
        run_config, _BYOK_LIGHTSPEED_STACK_CONFIG, env_overrides,
        provider=f"byok-{provider}", expected_model=config["model"],
        byok_vector_db_path="byok_vector_db",
    )
    try:
        yield config
    finally:
        _stop_sanity_server(process, runtime, name)
        if env_file_path:
            try:
                os.unlink(env_file_path)
            except OSError:
                pass


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL
