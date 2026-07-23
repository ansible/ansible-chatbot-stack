"""Tests for the system prompt rendering tool."""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "ansible-chatbot-system-prompt.txt.j2"
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render_system_prompt.py"


class TestRenderSystemPrompt:
    """Tests for render_system_prompt.py CLI tool."""

    def _run_render(self, env_overrides: dict, output_path: str) -> subprocess.CompletedProcess:
        """Helper to run the render script with given env vars."""
        env = os.environ.copy()
        env.update(env_overrides)
        return subprocess.run(
            [
                "python3",
                str(RENDER_SCRIPT),
                "--template",
                str(TEMPLATE_PATH),
                "--output",
                output_path,
            ],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_renders_openai_variant(self, tmp_path):
        """OpenAI provider should produce standard tool-call behavior."""
        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "gpt-4o-mini",
                "CHATBOT_LLM_PROVIDER_TYPE": "openai",
                "CHATBOT_BYOK_ENABLED": "false",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = output.read_text()
        assert "Automation Intelligent Assistant" in content
        assert "invoke it immediately and silently" in content
        assert "<|tool_call|>" not in content
        assert "ORGANIZATION_CONTENT_PRIORITY" not in content

    def test_renders_granite_variant(self, tmp_path):
        """Granite model on vLLM should produce custom tool-call format."""
        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "granite-3.3-8b-instruct",
                "CHATBOT_LLM_PROVIDER_TYPE": "rhoai_vllm",
                "CHATBOT_BYOK_ENABLED": "false",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = output.read_text()
        assert "Automation Intelligent Assistant" in content
        assert "<|tool_call|>" in content
        assert "ORGANIZATION_CONTENT_PRIORITY" not in content

    def test_renders_byok_sections(self, tmp_path):
        """BYOK enabled should include org-knowledge priority sections."""
        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "gpt-4o-mini",
                "CHATBOT_LLM_PROVIDER_TYPE": "openai",
                "CHATBOT_BYOK_ENABLED": "true",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = output.read_text()
        assert "ORGANIZATION_CONTENT_PRIORITY" in content
        assert "knowledge_search" in content

    def test_omits_byok_when_disabled(self, tmp_path):
        """BYOK disabled (default) should omit org-knowledge sections."""
        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "gpt-4o-mini",
                "CHATBOT_LLM_PROVIDER_TYPE": "openai",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = output.read_text()
        assert "ORGANIZATION_CONTENT_PRIORITY" not in content

    def test_custom_product_name(self, tmp_path):
        """Custom product name should appear in the identity section."""
        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "gpt-4o-mini",
                "CHATBOT_LLM_PROVIDER_TYPE": "openai",
                "CHATBOT_PRODUCT_NAME": "My Custom Assistant",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = output.read_text()
        assert "My Custom Assistant" in content
        assert "Automation Intelligent Assistant" not in content

    def test_default_product_name(self, tmp_path):
        """Default product name should be used when env var is not set."""
        output = tmp_path / "prompt.txt"
        env = {
            "INFERENCE_MODEL": "gpt-4o-mini",
            "CHATBOT_LLM_PROVIDER_TYPE": "openai",
        }
        env.pop("CHATBOT_PRODUCT_NAME", None)
        result = self._run_render(env, str(output))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        content = output.read_text()
        assert "Automation Intelligent Assistant" in content

    def test_fails_on_missing_template(self, tmp_path):
        """Should exit non-zero if template file doesn't exist."""
        output = tmp_path / "prompt.txt"
        result = subprocess.run(
            [
                "python3",
                str(RENDER_SCRIPT),
                "--template",
                "/nonexistent/template.j2",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert not output.exists()

    def test_creates_output_directory(self, tmp_path):
        """Should create parent directories for output file if needed."""
        output = tmp_path / "nested" / "dir" / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "gpt-4o-mini",
                "CHATBOT_LLM_PROVIDER_TYPE": "openai",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert output.exists()


class TestRegressionAgainstCurrentFiles:
    """Verify rendered output matches the current pre-rendered .txt files."""

    def _run_render(self, env_overrides: dict, output_path: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(env_overrides)
        return subprocess.run(
            [
                "python3",
                str(RENDER_SCRIPT),
                "--template",
                str(TEMPLATE_PATH),
                "--output",
                output_path,
            ],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_matches_openai_prompt(self, tmp_path):
        """Rendered OpenAI variant should match the existing .txt file."""
        current_file = REPO_ROOT / "ansible-chatbot-system-prompt.txt"
        if not current_file.exists():
            pytest.skip("Pre-rendered OpenAI prompt file not found (already removed)")

        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "gpt-4o-mini",
                "CHATBOT_LLM_PROVIDER_TYPE": "openai",
                "CHATBOT_BYOK_ENABLED": "false",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        expected = current_file.read_text().strip()
        actual = output.read_text().strip()
        assert actual == expected, (
            "Rendered OpenAI prompt differs from current file. "
            "Run diff to inspect differences."
        )

    def test_matches_granite_prompt(self, tmp_path):
        """Rendered Granite variant should match the existing .txt file."""
        current_file = REPO_ROOT / "ansible-chatbot-system-prompt-granite-compat.txt"
        if not current_file.exists():
            pytest.skip("Pre-rendered Granite prompt file not found (already removed)")

        output = tmp_path / "prompt.txt"
        result = self._run_render(
            {
                "INFERENCE_MODEL": "granite-3.3-8b-instruct",
                "CHATBOT_LLM_PROVIDER_TYPE": "rhoai_vllm",
                "CHATBOT_BYOK_ENABLED": "false",
            },
            str(output),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        expected = current_file.read_text().strip()
        actual = output.read_text().strip()
        assert actual == expected, (
            "Rendered Granite prompt differs from current file. "
            "Run diff to inspect differences."
        )
