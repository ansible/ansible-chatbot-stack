#!/usr/bin/env python3
"""
Start the granite sanity-test chatbot and write a HAR file for RapiDAST.

Reuses tests.sanity.conftest server lifecycle. MCP servers are not started.

Examples:
    uv run --frozen --group test python tests/dast/prepare.py
    uv run --frozen --group test python tests/dast/prepare.py --keep-alive
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

from tests.dast.har import DEFAULT_HAR_NAME, HarRecorder, run_probes  # noqa: E402
from tests.sanity.conftest import (  # noqa: E402
    BASE_URL,
    _LIGHTSPEED_STACK_CONFIG,
    _build_provider_config,
    _cleanup_vertex_adc_file,
    _start_sanity_server,
    _stop_sanity_server,
    _unlink_quietly,
)

READY_FILE_DEFAULT = "dast-target.ready"


def _secret_values(env_overrides):
    return [value for value in env_overrides.values() if value]


_ALLOWED_PATH_ROOTS = (_REPO_ROOT, Path(tempfile.gettempdir()).resolve())


def _resolve_under_repo(raw_path):
    """Resolve raw_path, rejecting anything outside the repo or the system temp dir."""
    path = Path(raw_path)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    resolved = path.resolve()
    if not any(
        resolved == root or root in resolved.parents for root in _ALLOWED_PATH_ROOTS
    ):
        raise ValueError(f"path escapes allowed directories: {raw_path}")
    return resolved


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Start the granite chatbot (sanity fixtures) and generate a DAST HAR."
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_HAR_NAME,
        help=f"HAR output path (default: {DEFAULT_HAR_NAME})",
    )
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Leave the chatbot running until SIGTERM/SIGINT (for RapiDAST).",
    )
    parser.add_argument(
        "--ready-file",
        default=READY_FILE_DEFAULT,
        help=f"Marker file written after the HAR is saved (default: {READY_FILE_DEFAULT})",
    )
    return parser.parse_args(argv)


def _wait_for_shutdown():
    done = threading.Event()

    def _stop(signum, _frame):
        print(f"[⏹] Received signal {signum}, shutting down...", flush=True)
        done.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print("[⏳] Chatbot left running for RapiDAST; waiting for SIGTERM/SIGINT", flush=True)
    done.wait()


def _require_granite_config():
    try:
        return _build_provider_config("granite")
    except pytest.skip.Exception as exc:
        print(f"Granite credentials are required for DAST: {exc}", file=sys.stderr)
        return None


def main(argv=None):
    args = parse_args(argv)
    if args.keep_alive and hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    try:
        output_path = _resolve_under_repo(args.output)
        ready_path = _resolve_under_repo(args.ready_file)
    except ValueError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 1

    previous_cwd = os.getcwd()
    process = runtime = name = env_file_path = credentials_file = None
    try:
        os.chdir(_REPO_ROOT)
        built = _require_granite_config()
        if built is None:
            return 1
        run_config, env_overrides, config = built
        credentials_file = config.pop("credentials_file", None)

        ready_path.unlink(missing_ok=True)

        process, runtime, name, env_file_path = _start_sanity_server(
            run_config,
            _LIGHTSPEED_STACK_CONFIG,
            env_overrides,
            "granite",
            config["model"],
        )
        recorder = HarRecorder()
        responses = run_probes(recorder, BASE_URL, config)
        if not responses or responses[0].status_code != 200:
            print("GET /v1/config did not return 200 — chatbot is not ready", file=sys.stderr)
            return 1
        recorder.save(output_path, redact_values=_secret_values(env_overrides))
        ready_path.write_text("ready\n", encoding="utf-8")
        print(
            f"[✓] HAR written to {output_path} ({len(recorder.entries)} entries)",
            flush=True,
        )
        if args.keep_alive:
            _wait_for_shutdown()
        return 0
    finally:
        _stop_sanity_server(process, runtime, name)
        _unlink_quietly(env_file_path)
        _cleanup_vertex_adc_file(credentials_file)
        os.chdir(previous_cwd)


if __name__ == "__main__":
    sys.exit(main())
