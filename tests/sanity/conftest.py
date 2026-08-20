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

from tests.sanity.mock_aap import port_is_in_use, start_mock_aap


BASE_URL = "http://127.0.0.1:8322"

_GRANITE_REQUIRED = ["VLLM_URL", "VLLM_API_TOKEN", "INFERENCE_MODEL"]
_OPENAI_REQUIRED = ["OPENAI_API_KEY", "OPENAI_INFERENCE_MODEL"]
_AZURE_REQUIRED = ["AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_INFERENCE_MODEL"]

_SANITY_DIR = Path(__file__).parent
_PROJECT_ROOT = _SANITY_DIR.parent.parent
_TEST_DATA_DIR = _PROJECT_ROOT / ".test_data"
_LIGHTSPEED_STACK_CONFIG = str(_SANITY_DIR / "lightspeed-stack.yaml")
_BYOK_LIGHTSPEED_STACK_CONFIG = str(_SANITY_DIR / "byok-lightspeed-stack.yaml")
_MCP_LIGHTSPEED_STACK_CONFIG = str(_SANITY_DIR / "mcp-lightspeed-stack.yaml")

_GRANITE_SYSTEM_PROMPT_FILE = "ansible-chatbot-system-prompt-granite-compat.txt"
_DEFAULT_SYSTEM_PROMPT_FILE = "ansible-chatbot-system-prompt.txt"

_PROVIDER_VECTOR_DB_ID_FILE = "provider_vector_db_id.ind"

_ALLOWED_RUNTIMES = ("podman", "docker")
_IMAGE_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-/:]{0,253}$")
_DEFAULT_MCP_CONTROLLER_IMAGE = "quay.io/ansible/ansible-mcp-controller:latest"
_DEFAULT_MCP_LIGHTSPEED_IMAGE = "quay.io/ansible/ansible-mcp-lightspeed:latest"
_MCP_CONTROLLER_PORT = 8004
_MCP_LIGHTSPEED_PORT = 8005
_DEFAULT_MCP_AAP_MOCK_PORT = 18080
MCP_AUTH_TOKEN = "Bearer sanity-token"

# Shared chatbot log buffer so MCP tests can inspect tool-filter output.
_CHATBOT_OUTPUT_LINES: list[str] = []
_CHATBOT_OUTPUT_LOCK = threading.Lock()
# The stdout/stderr capture threads for the currently (or most recently) running
# container, so _stop_sanity_server can join them before the next provider's
# _start_sanity_server clears _CHATBOT_OUTPUT_LINES out from under them.
_CHATBOT_CAPTURE_THREADS: list[threading.Thread] = []

_TRUTHY = ("1", "true", "yes", "on")


def pytest_addoption(parser):
    parser.addoption(
        "--mcp-debug",
        action="store_true",
        default=False,
        help=(
            "Print MCP tool-filter counts (tools in → tools kept). "
            "Also enabled when MCP_DEBUG=1."
        ),
    )


def mcp_debug_enabled(config=None):
    """Return True when --mcp-debug or MCP_DEBUG is set."""
    if os.environ.get("MCP_DEBUG", "").strip().lower() in _TRUTHY:
        return True
    if config is None:
        return False
    try:
        return bool(config.getoption("--mcp-debug"))
    except ValueError:
        return False


@pytest.fixture
def mcp_filter_debug(request, capfd):
    """Callable that prints a before/after tool-filter summary when debug is on."""

    def _report(output_lines, query=""):
        if not mcp_debug_enabled(request.config):
            return
        from tests.sanity.test_mcp import format_filter_debug

        message = format_filter_debug(output_lines, query)
        # Default capture is fd-level; write_line/print are swallowed on PASS.
        with capfd.disabled():
            sys.stderr.write(message + "\n")
            sys.stderr.flush()

    return _report


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


def _build_mcp_provider_config(provider):
    """Return MCP-enabled run config, env overrides, and provider config dict."""
    _, env_overrides, config = _build_provider_config(provider)
    run_config = str(_SANITY_DIR / f"mcp-{provider}-chatbot-run.yaml")
    if not Path(run_config).exists():
        pytest.fail(f"MCP run config not found: {run_config}")
    if os.environ.get("INFERENCE_MODEL_FILTER"):
        env_overrides["INFERENCE_MODEL_FILTER"] = os.environ["INFERENCE_MODEL_FILTER"]
    return run_config, env_overrides, dict(config)


def _get_container_runtime():
    """Return an allowlisted container runtime path or fail the test."""
    requested = os.environ.get("CONTAINER_RUNTIME", "")
    if requested and requested not in _ALLOWED_RUNTIMES:
        pytest.fail(f"CONTAINER_RUNTIME must be one of {_ALLOWED_RUNTIMES}, got: {requested!r}")
    container_runtime = shutil.which(requested) if requested else None
    if not container_runtime:
        container_runtime = shutil.which("podman") or shutil.which("docker")
    if not container_runtime:
        pytest.fail("Container runtime (podman/docker) not found")
    return container_runtime


def _validate_image_ref(image):
    """Reject image refs that could break out of the subprocess argv."""
    if not _IMAGE_REF_RE.fullmatch(image) or ".." in image:
        pytest.fail(f"Invalid container image reference: {image!r}")
    return image


def _system_prompt_file_for(provider):
    """
    Granite models require the '<|tool_call|>[...]' literal-token format documented
    in ansible-chatbot-system-prompt-granite-compat.txt (see README's System Prompt
    table) for a self-hosted vLLM tool-call-parser to recognize tool calls at all.
    `provider` may be a composite label like 'byok-granite' or 'mcp-granite'.
    """
    return _GRANITE_SYSTEM_PROMPT_FILE if provider.endswith("granite") else _DEFAULT_SYSTEM_PROMPT_FILE


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
    system_prompt_file = _system_prompt_file_for(provider)
    if not Path(f"./{system_prompt_file}").exists():
        pytest.fail(f"{system_prompt_file} not found in repo root")
    if not (_TEST_DATA_DIR / "embeddings_model").exists():
        pytest.fail(".test_data/embeddings_model directory not found — run 'make setup-sanity-test-data' first")
    if not (_TEST_DATA_DIR / "vector_db" / "aap_faiss_store.db").exists():
        pytest.fail(".test_data/vector_db/aap_faiss_store.db not found — run 'make setup-sanity-test-data' first")
    if byok_vector_db_path and not (_TEST_DATA_DIR / byok_vector_db_path / "faiss_store.db").exists():
        pytest.fail(f".test_data/{byok_vector_db_path}/faiss_store.db not found — run 'make setup-sanity-test-data' first")
    if byok_vector_db_path and not os.environ.get("BYOK_PROVIDER_VECTOR_DB_ID"):
        byok_ind_path = _TEST_DATA_DIR / byok_vector_db_path / _PROVIDER_VECTOR_DB_ID_FILE
        if not byok_ind_path.exists() or not byok_ind_path.read_text().strip():
            pytest.fail(
                f"{byok_ind_path} missing or empty — BYOK_PROVIDER_VECTOR_DB_ID would resolve "
                "to an empty string and BYOK retrieval would be silently inert. "
                "Run 'make setup-sanity-test-data' first."
            )
    if not Path("./llama-stack/providers.d").exists():
        pytest.fail("llama-stack/providers.d directory not found — run 'make setup-test' first")

    # Resolve container runtime — allowlist prevents arbitrary command injection
    container_runtime = _get_container_runtime()
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
    vid_file = _TEST_DATA_DIR / "vector_db" / _PROVIDER_VECTOR_DB_ID_FILE
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
        "-v", f"{_TEST_DATA_DIR}/embeddings_model:/.llama/data/embeddings_model{selinux_flag}",
        "-v", f"{_TEST_DATA_DIR}/vector_db/aap_faiss_store.db:/.llama/data/distributions/ansible-chatbot/aap_faiss_store.db{selinux_flag}",
        "-v", f"{lightspeed_config_path}:/.llama/distributions/ansible-chatbot/config/lightspeed-stack.yaml{selinux_flag}",
        "-v", f"{run_config_path}:/.llama/distributions/llama-stack/config/ansible-chatbot-run.yaml{selinux_flag}",
        "-v", f"{Path.cwd()}/{system_prompt_file}:/.llama/distributions/ansible-chatbot/system-prompts/default.txt{selinux_flag}",
        "-v", f"{Path.cwd()}/llama-stack/providers.d:/.llama/providers.d{selinux_flag}",
        "--env", f"PROVIDER_VECTOR_DB_ID={provider_vector_db_id}",
        "--env", "PYTHONUNBUFFERED=1",
        "--env", f"LOG_LEVEL={os.environ.get('LOG_LEVEL', 'INFO')}",
        "--env-file", env_file_path,
    ]

    if byok_vector_db_path:
        byok_vid_file = _TEST_DATA_DIR / byok_vector_db_path / _PROVIDER_VECTOR_DB_ID_FILE
        byok_vector_db_id = os.environ.get("BYOK_PROVIDER_VECTOR_DB_ID", "")
        if byok_vid_file.exists() and not byok_vector_db_id:
            try:
                byok_vector_db_id = byok_vid_file.read_text().strip()
            except OSError:
                pass
        cmd += [
            "-v", f"{_TEST_DATA_DIR / byok_vector_db_path}:/.llama/data/byok/distributions/ansible-chatbot{selinux_flag}",
            "--env", f"BYOK_PROVIDER_VECTOR_DB_ID={byok_vector_db_id}",
        ]

    cmd.append(full_image)

    with _CHATBOT_OUTPUT_LOCK:
        _CHATBOT_OUTPUT_LINES.clear()
    output_lines = _CHATBOT_OUTPUT_LINES

    def _capture(pipe, prefix=""):
        try:
            for line in iter(pipe.readline, ""):
                if line:
                    stripped = line.rstrip()
                    with _CHATBOT_OUTPUT_LOCK:
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

    stdout_thread = threading.Thread(target=_capture, args=(process.stdout, "[STDOUT] "), daemon=True)
    stderr_thread = threading.Thread(target=_capture, args=(process.stderr, "[STDERR] "), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    _CHATBOT_CAPTURE_THREADS[:] = [stdout_thread, stderr_thread]

    max_wait = int(os.environ.get("SERVER_STARTUP_TIMEOUT", "300"))
    print(f"[⏳] Waiting for server to be ready (max {max_wait}s)...")

    # Use a wall-clock deadline so the budget is measured in real seconds,
    # not iterations (each iteration can take up to 3s with a 2s request timeout).
    start = time.monotonic()
    deadline = start + max_wait
    last_progress = start
    while time.monotonic() < deadline:
        if process.poll() is not None:
            with _CHATBOT_OUTPUT_LOCK:
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

    with _CHATBOT_OUTPUT_LOCK:
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

    # Drain the capture threads before returning so a still-appending thread from
    # this container can't race the next provider's _CHATBOT_OUTPUT_LINES.clear().
    # The process (and its stdout/stderr pipes) is already closed at this point,
    # so each thread's iter(pipe.readline, "") loop hits EOF and exits promptly.
    for thread in _CHATBOT_CAPTURE_THREADS:
        thread.join(timeout=5)

    if container_runtime and container_name:
        try:
            subprocess.run([container_runtime, "stop", container_name], capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            subprocess.run([container_runtime, "rm", "-f", container_name], capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            pass

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


def _mcp_images():
    """
    Resolve controller and lightspeed MCP image refs.

    quay.io/ansible/ansible-mcp-tool is not publicly pullable; the published
    images are ansible-mcp-controller and ansible-mcp-lightspeed. Set MCP_IMAGE
    to force a single dual-service image for both containers.
    """
    unified = os.environ.get("MCP_IMAGE", "").strip()
    if unified:
        image = _validate_image_ref(unified)
        return image, image
    controller = os.environ.get("MCP_CONTROLLER_IMAGE", _DEFAULT_MCP_CONTROLLER_IMAGE)
    lightspeed = os.environ.get("MCP_LIGHTSPEED_IMAGE", _DEFAULT_MCP_LIGHTSPEED_IMAGE)
    return _validate_image_ref(controller), _validate_image_ref(lightspeed)


def _mcp_image_explicitly_configured():
    """True when the caller pinned an MCP image via env rather than relying on the default."""
    return bool(
        os.environ.get("MCP_IMAGE", "").strip()
        or os.environ.get("MCP_CONTROLLER_IMAGE", "").strip()
        or os.environ.get("MCP_LIGHTSPEED_IMAGE", "").strip()
    )


def _skip_or_fail_unpullable(message):
    """
    Skip locally, but fail loud in CI.

    A silent skip is fine on a laptop without registry access. In CI, GitHub Actions
    sets CI=true, and the workflow always pins MCP_CONTROLLER_IMAGE/MCP_LIGHTSPEED_IMAGE
    — an unpullable image there means the whole MCP suite has never executed, which
    must fail the build rather than report a deceptively green run.
    """
    in_ci = os.environ.get("CI", "").strip().lower() in _TRUTHY
    if in_ci and _mcp_image_explicitly_configured():
        pytest.fail(message)
    pytest.skip(message)


def _ensure_image(container_runtime, image):
    """Pull image if missing; skip locally (fail in CI) when the registry is unavailable."""
    inspect = subprocess.run(
        [container_runtime, "image", "inspect", image],
        capture_output=True,
    )
    if inspect.returncode == 0:
        print(f"[✓] MCP image present: {image}")
        return
    print(f"[⚙] Pulling MCP image {image}...")
    try:
        pull = subprocess.run(
            [container_runtime, "pull", image],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        _skip_or_fail_unpullable(f"Timed out pulling MCP image {image}")
        return
    if pull.returncode != 0:
        detail = (pull.stderr or pull.stdout or "").strip()[-400:]
        _skip_or_fail_unpullable(f"Could not pull MCP image {image}: {detail}")
        return
    print(f"[✓] Pulled MCP image {image}")


def _assert_port_free(port, label, wait=10):
    """Fail unless the port is free, allowing a brief window for the previous
    provider's container teardown (podman rm -f) to actually release it."""
    deadline = time.monotonic() + wait
    while port_is_in_use(port):
        if time.monotonic() >= deadline:
            pytest.fail(
                f"{label} port {port} is already in use. "
                "Stop the existing process/container before MCP sanity tests."
            )
        time.sleep(0.5)


def _wait_for_tcp(port, timeout=60, label="service"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_in_use(port):
            print(f"[✓] {label} listening on port {port}")
            return
        time.sleep(0.5)
    pytest.fail(f"{label} did not listen on port {port} within {timeout}s")


def _start_mcp_container(container_runtime, image, name, port, aap_url):
    """Start one MCP server on the host network. Returns process, env file, log file, and log handle."""
    env_file = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    env_file_path = env_file.name
    try:
        env_file.write(f"AAP_GATEWAY_URL={aap_url}\n")
        env_file.write(f"AAP_SERVICE_URL={aap_url}\n")
        env_file.write("HOST=0.0.0.0\n")
        env_file.write(f"PORT={port}\n")
        env_file.write("PYTHONUNBUFFERED=1\n")
    finally:
        env_file.close()
    os.chmod(env_file_path, 0o600)

    # MCP servers log OpenAPI parsing at DEBUG (~tens of thousands of lines).
    # Keep that off the pytest console; dump a tail only on failure.
    log_file = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
    log_file_path = log_file.name
    log_file.close()
    log_handle = open(log_file_path, "w", encoding="utf-8")

    cmd = [
        container_runtime, "run",
        "--rm",
        "--name", name,
        "--platform", "linux/amd64",
        "--security-opt", "label=disable",
        "--network", "host",
        "--env-file", env_file_path,
        image,
    ]
    print(f"[⚙] Starting MCP container {name} ({image}) on port {port}")
    try:
        process = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        log_handle.close()
        try:
            os.unlink(log_file_path)
            os.unlink(env_file_path)
        except OSError:
            pass
        raise
    return process, env_file_path, log_file_path, log_handle


def _dump_mcp_log_tail(name, log_file_path, lines=40):
    if not log_file_path or not Path(log_file_path).exists():
        return
    try:
        text = Path(log_file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    tail = text.splitlines()[-lines:]
    if not tail:
        return
    sys.stderr.write(f"\n[✗] Last {len(tail)} log lines from {name}:\n")
    for line in tail:
        sys.stderr.write(line + "\n")


def _stop_mcp_container(process, container_runtime, name, env_file_path, log_file_path=None, log_handle=None):
    if process and process.poll() is None:
        print(f"[⏹] Stopping MCP container {name}...")
        try:
            process.terminate()
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
    if container_runtime and name:
        try:
            subprocess.run([container_runtime, "rm", "-f", name], capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            pass
    if log_handle:
        try:
            log_handle.close()
        except OSError:
            pass
    if env_file_path:
        try:
            os.unlink(env_file_path)
        except OSError:
            pass
    if log_file_path:
        try:
            os.unlink(log_file_path)
        except OSError:
            pass


def _start_mcp_servers(aap_url):  # noqa: cognitive-complexity
    """Pull and start controller + lightspeed MCP servers. Returns handle list."""
    container_runtime = _get_container_runtime()
    controller_image, lightspeed_image = _mcp_images()
    _ensure_image(container_runtime, controller_image)
    _ensure_image(container_runtime, lightspeed_image)

    _assert_port_free(_MCP_CONTROLLER_PORT, "MCP controller")
    _assert_port_free(_MCP_LIGHTSPEED_PORT, "MCP lightspeed")

    pid = os.getpid()
    handles = []
    controller_name = f"ansible-mcp-controller-sanity-{pid}"
    lightspeed_name = f"ansible-mcp-lightspeed-sanity-{pid}"

    process, env_file, log_file, log_handle = _start_mcp_container(
        container_runtime, controller_image, controller_name,
        _MCP_CONTROLLER_PORT, aap_url,
    )
    handles.append((process, container_runtime, controller_name, env_file, log_file, log_handle))
    process, env_file, log_file, log_handle = _start_mcp_container(
        container_runtime, lightspeed_image, lightspeed_name,
        _MCP_LIGHTSPEED_PORT, aap_url,
    )
    handles.append((process, container_runtime, lightspeed_name, env_file, log_file, log_handle))

    try:
        for process, _runtime, name, _env, log_file, _lh in handles:
            deadline = time.monotonic() + 45
            port = _MCP_CONTROLLER_PORT if "controller" in name else _MCP_LIGHTSPEED_PORT
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    _dump_mcp_log_tail(name, log_file)
                    pytest.fail(f"MCP container {name} exited during startup (code {process.poll()})")
                if port_is_in_use(port):
                    break
                time.sleep(0.5)
        _wait_for_tcp(_MCP_CONTROLLER_PORT, timeout=60, label="MCP controller")
        _wait_for_tcp(_MCP_LIGHTSPEED_PORT, timeout=60, label="MCP lightspeed")
    except Exception:
        for process, runtime, name, env_file, log_file, log_handle in handles:
            _dump_mcp_log_tail(name, log_file)
            _stop_mcp_container(process, runtime, name, env_file, log_file, log_handle)
        raise
    return handles


def _stop_mcp_servers(handles):
    for item in reversed(handles or []):
        process, runtime, name, env_file, *rest = item
        log_file = rest[0] if rest else None
        log_handle = rest[1] if len(rest) > 1 else None
        _stop_mcp_container(process, runtime, name, env_file, log_file, log_handle)


def _start_mock_aap_for_sanity():
    requested = os.environ.get("MCP_AAP_MOCK_PORT", "").strip()
    if requested:
        if not requested.isdigit():
            pytest.fail(f"MCP_AAP_MOCK_PORT must be a positive integer, got: {requested!r}")
        port = int(requested)
        if port_is_in_use(port):
            pytest.fail(f"Mock AAP port {port} is already in use")
        mock = start_mock_aap(port)
    elif port_is_in_use(_DEFAULT_MCP_AAP_MOCK_PORT):
        print(f"[⚙] Default mock AAP port {_DEFAULT_MCP_AAP_MOCK_PORT} busy — using ephemeral port")
        mock = start_mock_aap(0)
    else:
        mock = start_mock_aap(_DEFAULT_MCP_AAP_MOCK_PORT)
    print(f"[✓] Mock AAP listening at {mock.url}")
    return mock


# ---------------------------------------------------------------------------
# Parametrized provider fixture
# ---------------------------------------------------------------------------

@pytest.fixture(
    params=[
        pytest.param("granite", marks=pytest.mark.granite),
        pytest.param("openai", marks=pytest.mark.openai),
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
        pytest.param("openai", marks=[pytest.mark.openai, pytest.mark.byok]),
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
    try:
        resp = requests.get(f"{BASE_URL}/v1/config", timeout=2)
        if resp.status_code == 200:
            pytest.fail(
                f"Chatbot already running at {BASE_URL}. _start_sanity_server's reuse "
                "check only verifies the model name, not that BYOK config is mounted — "
                "stop the existing server before BYOK sanity tests."
            )
    except requests.exceptions.RequestException:
        pass

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


@pytest.fixture(
    params=[
        pytest.param("granite", marks=[pytest.mark.granite, pytest.mark.mcp]),
        pytest.param("openai", marks=[pytest.mark.openai, pytest.mark.mcp]),
        pytest.param("azure", marks=[pytest.mark.azure, pytest.mark.mcp]),
    ],
    scope="module",
)
def mcp_provider_setup(request):
    """
    Start mock AAP, controller/lightspeed MCP servers, and the chatbot with MCP enabled.

    Yields a dict with keys 'model', 'provider', 'mock_aap', and 'output_lines'.
    Skips when inference env vars are missing or MCP images cannot be pulled.
    """
    try:
        resp = requests.get(f"{BASE_URL}/v1/config", timeout=2)
        if resp.status_code == 200:
            pytest.fail(
                f"Chatbot already running at {BASE_URL}. "
                "Stop it before MCP sanity tests so MCP sidecars can be attached."
            )
    except requests.exceptions.RequestException:
        pass

    provider = request.param
    run_config, env_overrides, config = _build_mcp_provider_config(provider)
    mock_aap = _start_mock_aap_for_sanity()
    mcp_handles = []
    process = runtime = name = env_file_path = None
    try:
        mcp_handles = _start_mcp_servers(mock_aap.url)
        process, runtime, name, env_file_path = _start_sanity_server(
            run_config, _MCP_LIGHTSPEED_STACK_CONFIG, env_overrides,
            provider=f"mcp-{provider}", expected_model=config["model"],
        )
        config["mock_aap"] = mock_aap
        config["output_lines"] = _CHATBOT_OUTPUT_LINES
        yield config
    finally:
        _stop_sanity_server(process, runtime, name)
        if env_file_path:
            try:
                os.unlink(env_file_path)
            except OSError:
                pass
        _stop_mcp_servers(mcp_handles)
        mock_aap.stop()


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL
