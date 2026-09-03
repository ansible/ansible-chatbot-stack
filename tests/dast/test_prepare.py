"""Unit tests for the DAST prepare CLI (no live chatbot or LLM)."""

from datetime import datetime, timezone
import json

import pytest

from tests.dast import prepare
from tests.dast.har import CONFIG_PATH, Probe


@pytest.fixture(autouse=True)
def _never_start_container(monkeypatch):
    monkeypatch.setattr(
        prepare,
        "_start_sanity_server",
        lambda *args, **kwargs: (None, None, None, None),
    )
    monkeypatch.setattr(prepare, "_stop_sanity_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(prepare, "_unlink_quietly", lambda path: None)
    monkeypatch.setattr(prepare, "_cleanup_vertex_adc_file", lambda path: None)


def test_prepare_fails_without_granite_credentials(monkeypatch, capsys):
    for var in ("VLLM_URL", "VLLM_API_TOKEN", "INFERENCE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert prepare.main(["--output", "unused.har"]) == 1
    captured = capsys.readouterr()
    assert "Granite credentials are required" in captured.err


def test_prepare_writes_har_and_ready_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VLLM_URL", "http://vllm.example")
    monkeypatch.setenv("VLLM_API_TOKEN", "token")
    monkeypatch.setenv("INFERENCE_MODEL", "granite-demo")

    class _Resp:
        status_code = 200

    def _fake_probes(recorder, base_url, config):
        recorder.add_entry(
            method="GET",
            url=f"{base_url}/v1/config",
            status=200,
            reason="OK",
            started=datetime.now(timezone.utc),
            duration_ms=1.0,
            response_body=b'{"ok":true}',
        )
        return [(Probe("GET", CONFIG_PATH), _Resp())]

    monkeypatch.setattr(prepare, "run_probes", _fake_probes)

    har_path = tmp_path / "chatbot-requests.har"
    ready_path = tmp_path / "dast-target.ready"
    assert prepare.main(["--output", str(har_path), "--ready-file", str(ready_path)]) == 0
    assert har_path.is_file()
    assert ready_path.read_text() == "ready\n"
    payload = json.loads(har_path.read_text())
    assert len(payload["log"]["entries"]) == 1


def test_prepare_rejects_output_path_outside_allowed_roots(monkeypatch, capsys):
    monkeypatch.setenv("VLLM_URL", "http://vllm.example")
    monkeypatch.setenv("VLLM_API_TOKEN", "token")
    monkeypatch.setenv("INFERENCE_MODEL", "granite-demo")

    assert prepare.main(["--output", "/etc/passwd"]) == 1
    captured = capsys.readouterr()
    assert "Refusing to run" in captured.err


def test_prepare_rejects_ready_file_outside_allowed_roots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("VLLM_URL", "http://vllm.example")
    monkeypatch.setenv("VLLM_API_TOKEN", "token")
    monkeypatch.setenv("INFERENCE_MODEL", "granite-demo")

    har_path = tmp_path / "chatbot-requests.har"
    assert (
        prepare.main(["--output", str(har_path), "--ready-file", "../../../../etc/cron.d/x"])
        == 1
    )
    captured = capsys.readouterr()
    assert "Refusing to run" in captured.err


def test_prepare_fails_when_config_probe_is_not_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("VLLM_URL", "http://vllm.example")
    monkeypatch.setenv("VLLM_API_TOKEN", "token")
    monkeypatch.setenv("INFERENCE_MODEL", "granite-demo")

    class _Resp:
        status_code = 503

    monkeypatch.setattr(
        prepare, "run_probes", lambda *args, **kwargs: [(Probe("GET", CONFIG_PATH), _Resp())]
    )
    har_path = tmp_path / "chatbot-requests.har"
    assert prepare.main(["--output", str(har_path)]) == 1
    assert not har_path.exists()


def test_prepare_fails_when_config_probe_missing_even_if_other_probe_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("VLLM_URL", "http://vllm.example")
    monkeypatch.setenv("VLLM_API_TOKEN", "token")
    monkeypatch.setenv("INFERENCE_MODEL", "granite-demo")

    class _Resp:
        status_code = 200

    monkeypatch.setattr(
        prepare,
        "run_probes",
        lambda *args, **kwargs: [(Probe("GET", "/v1/models"), _Resp())],
    )
    har_path = tmp_path / "chatbot-requests.har"
    assert prepare.main(["--output", str(har_path)]) == 1
    assert not har_path.exists()
