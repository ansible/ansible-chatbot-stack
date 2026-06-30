#!/usr/bin/env python3
"""Generate system prompt files from the upstream canonical Jinja2 template.

Downloads the chatbot system prompt template from ansible-ai-connect-operator
and renders it with the appropriate variables to produce:
  - ansible-chatbot-system-prompt.txt (openai provider)
  - ansible-chatbot-system-prompt-granite-compat.txt (rhoai_vllm + granite model)
"""

import os
import sys
import urllib.request

import jinja2

TEMPLATE_URL = (
    "https://raw.githubusercontent.com/ansible/ansible-ai-connect-operator/"
    "refs/heads/main/roles/chatbot/templates/"
    "ansible-chatbot-system-prompt.txt.j2"
)

PRODUCT_NAME = "Automation Intelligent Assistant"

OUTPUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VARIANTS = [
    {
        "output_file": "ansible-chatbot-system-prompt.txt",
        "variables": {
            "chatbot_llm_provider_type": "openai",
            "chatbot_model": "gpt-4o-mini",
        },
    },
    {
        "output_file": "ansible-chatbot-system-prompt-granite-compat.txt",
        "variables": {
            "chatbot_llm_provider_type": "rhoai_vllm",
            "chatbot_model": "granite-3.3-8b-instruct",
        },
    },
]


def download_template(url):
    with urllib.request.urlopen(url) as resp:
        return resp.read().decode("utf-8")


def render_template(template_source, variables):
    env = jinja2.Environment(
        undefined=jinja2.Undefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.from_string(template_source)
    return template.render(
        chatbot_product_name=PRODUCT_NAME,
        **variables,
    )


def main():
    print(f"Downloading template from:\n  {TEMPLATE_URL}")
    template_source = download_template(TEMPLATE_URL)
    print("Template downloaded successfully.\n")

    for variant in VARIANTS:
        output_path = os.path.join(OUTPUT_DIR, variant["output_file"])
        print(f"Generating {variant['output_file']}...")
        print(f"  provider_type={variant['variables']['chatbot_llm_provider_type']}")
        if variant["variables"].get("chatbot_model"):
            print(f"  model={variant['variables']['chatbot_model']}")

        prompt = render_template(template_source, variant["variables"])

        with open(output_path, "w") as f:
            f.write(prompt + "\n")

        print(f"  Written to: {output_path}\n")

    print("Done.")


if __name__ == "__main__":
    main()
