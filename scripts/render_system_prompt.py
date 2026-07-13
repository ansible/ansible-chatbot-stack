#!/usr/bin/env python3
"""Render the system prompt Jinja template using environment variables.

Reads configuration from env vars and renders the canonical system prompt
template to produce the final prompt text for the chatbot.

Environment variables:
    INFERENCE_MODEL             Model name (e.g. "granite-3.3-8b-instruct")
    CHATBOT_LLM_PROVIDER_TYPE   Provider type (default: "rhoai_vllm")
    CHATBOT_BYOK_ENABLED        Enable BYOK sections (default: "false")
    CHATBOT_PRODUCT_NAME        Assistant name (default: "Automation Intelligent Assistant")

Usage:
    python3 render_system_prompt.py --template <path> --output <path>
"""

import argparse
import os
import sys
from pathlib import Path

import jinja2


DEFAULT_PRODUCT_NAME = "Automation Intelligent Assistant"
DEFAULT_PROVIDER_TYPE = "rhoai_vllm"


def build_context() -> dict:
    """Build Jinja template context from environment variables."""
    context = {
        "chatbot_product_name": os.getenv(
            "CHATBOT_PRODUCT_NAME", DEFAULT_PRODUCT_NAME
        ),
        "chatbot_llm_provider_type": os.getenv(
            "CHATBOT_LLM_PROVIDER_TYPE", DEFAULT_PROVIDER_TYPE
        ),
        "chatbot_model": os.getenv("INFERENCE_MODEL", ""),
    }

    if os.getenv("CHATBOT_BYOK_ENABLED", "false").lower() == "true":
        context["_chatbot_byok_image"] = "enabled"

    return context


def render_template(template_path: Path, context: dict) -> str:
    """Load and render the Jinja template with the given context."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_path.parent)),
        undefined=jinja2.Undefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template(template_path.name)
    return template.render(**context)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render system prompt from Jinja template using env vars."
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Path to the Jinja2 template file",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the rendered prompt",
    )
    args = parser.parse_args()

    template_path = Path(args.template)
    output_path = Path(args.output)

    if not template_path.exists():
        print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
        return 1

    context = build_context()

    print(f"Rendering system prompt from {template_path}")
    print(f"  chatbot_model={context['chatbot_model']}")
    print(f"  chatbot_llm_provider_type={context['chatbot_llm_provider_type']}")
    print(f"  chatbot_product_name={context['chatbot_product_name']}")
    print(f"  byok={'enabled' if '_chatbot_byok_image' in context else 'disabled'}")

    try:
        rendered = render_template(template_path, context)
    except jinja2.TemplateError as e:
        print(f"ERROR: Template rendering failed: {e}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)
    print(f"System prompt written to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
