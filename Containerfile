# Build arguments declared in the global scope.
ARG ANSIBLE_CHATBOT_VERSION=latest

# ======================================================
# Transient image to construct Python venv
# ------------------------------------------------------
FROM registry.access.redhat.com/ubi9/ubi-minimal AS builder

ARG APP_ROOT=/app-root
RUN microdnf install -y --nodocs --setopt=keepcache=0 --setopt=tsflags=nodocs \
    python3.12 python3.12-devel python3.12-pip
WORKDIR /app-root

# UV_PYTHON_DOWNLOADS=0 : Disable Python interpreter downloads and use the system interpreter.
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Install uv package manager
RUN pip3.12 install uv

# Add explicit files and directories
# (avoid accidental inclusion of local directories or env files or credentials)
COPY pyproject.toml uv.lock LICENSE.md README.md ./

RUN uv sync --locked --no-install-project --no-dev
# ======================================================

# ======================================================
# Final image without uv package manager
# ------------------------------------------------------
#FROM quay.io/lightspeed-core/lightspeed-stack:dev-latest
# the lastest was broken, replace by the latest working image for now
FROM quay.io/lightspeed-core/lightspeed-stack:dev-20250722-28abfaf

USER 0

# Re-declaring arguments without a value, inherits the global default one.
ARG APP_ROOT
ARG ANSIBLE_CHATBOT_VERSION
RUN microdnf install -y --nodocs --setopt=keepcache=0 --setopt=tsflags=nodocs jq

# PYTHONDONTWRITEBYTECODE 1 : disable the generation of .pyc
# PYTHONUNBUFFERED 1 : force the stdout and stderr streams to be unbuffered
# PYTHONCOERCECLOCALE 0, PYTHONUTF8 1 : skip legacy locales and use UTF-8 mode
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONCOERCECLOCALE=0 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=UTF-8 \
    LANG=en_US.UTF-8

COPY --from=builder /app-root /app-root

# this directory is checked by ecosystem-cert-preflight-checks task in Konflux
COPY --from=builder /app-root/LICENSE.md /licenses/

# Add executables from .venv to system PATH
ENV PATH="/app-root/.venv/bin:$PATH"

ENV LLAMA_STACK_CONFIG_DIR=/.llama/data

# Data and configuration
RUN mkdir -p /.llama/distributions/ansible-chatbot
RUN mkdir -p /.llama/data/distributions/ansible-chatbot
RUN echo -e "\
{\n\
  \"version\": \"${ANSIBLE_CHATBOT_VERSION}\" \n\
}\n\
" > /.llama/distributions/ansible-chatbot/ansible-chatbot-version-info.json
ADD llama-stack/providers.d /.llama/providers.d
RUN chmod -R g+rw /.llama

# Bootstrap
ADD entrypoint.sh /.llama
RUN chmod +x /.llama/entrypoint.sh

# See https://github.com/meta-llama/llama-stack/issues/1633
# USER 1001

ENTRYPOINT ["/.llama/entrypoint.sh"]
# ======================================================
