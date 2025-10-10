# Build arguments declared in the global scope.
ARG ANSIBLE_CHATBOT_VERSION=latest
ARG IMAGE_TAGS=image-tags-not-defined
ARG GIT_COMMIT=git-commit-not-defined

# ======================================================
# Transient image to construct Python venv
# ------------------------------------------------------
FROM quay.io/lightspeed-core/lightspeed-stack:0.3.0 AS builder

ARG APP_ROOT=/app-root
WORKDIR /app-root

# UV_PYTHON_DOWNLOADS=0 : Disable Python interpreter downloads and use the system interpreter.
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Install uv package manager
RUN pip3.12 install uv

# Add explicit files and directories
# (avoid accidental inclusion of local directories or env files or credentials)
COPY requirements.txt LICENSE.md README.md ./

RUN uv pip install -r requirements.txt
# ======================================================

# ======================================================
# Final image without uv package manager and based on lightspeed-stack base image
# ------------------------------------------------------
FROM registry.access.redhat.com/ubi9/python-312-minimal

USER 0

# Re-declaring arguments without a value, inherits the global default one.
ARG APP_ROOT
ARG ANSIBLE_CHATBOT_VERSION
ARG IMAGE_TAGS
ARG GIT_COMMIT
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

COPY --from=builder --chown=1001:1001 /app-root /app-root

# this directory is checked by ecosystem-cert-preflight-checks task in Konflux
COPY --from=builder /app-root/LICENSE.md /licenses/

ENV LLAMA_STACK_CONFIG_DIR=/.llama/data

# Data and configuration
RUN mkdir -p /.llama/distributions/ansible-chatbot
RUN mkdir -p /.llama/data/distributions/ansible-chatbot
RUN echo -e "\
{\n\
  \"version\": \"${ANSIBLE_CHATBOT_VERSION}\", \n\
  \"imageTags\": \"${IMAGE_TAGS}\", \n\
  \"gitCommit\": \"${GIT_COMMIT}\" \n\
}\n\
" > /.llama/distributions/ansible-chatbot/ansible-chatbot-version-info.json
ADD llama-stack/providers.d /.llama/providers.d

# Bootstrap
ADD entrypoint.sh /.llama

RUN chmod -R g+rw /.llama
RUN chmod +x /.llama/entrypoint.sh

RUN chown -R 1001:1001 /.llama

USER 1001

# Add executables from .venv to system PATH

ENV PATH="/app-root/.venv/bin:$PATH"
LABEL vendor="Red Hat, Inc."

ENTRYPOINT ["/.llama/entrypoint.sh", "/app-root/.venv/bin/python3.12"]
# ======================================================
