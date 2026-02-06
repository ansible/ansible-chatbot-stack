#!/bin/bash
LS_PROVIDERS_VERSION=0.4.2
OUT_DIR=llama-stack/providers.d/inline/agents
AGENT_YAML=lightspeed_inline_agent.yaml

mkdir -p ${OUT_DIR}
curl -o ${OUT_DIR}/${AGENT_YAML} \
  https://raw.githubusercontent.com/lightspeed-core/lightspeed-providers/refs/tags/\
${LS_PROVIDERS_VERSION}/resources/external_providers/inline/agents/${AGENT_YAML}
