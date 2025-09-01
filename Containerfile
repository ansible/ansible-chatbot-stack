# Build arguments declared in the global scope.
ARG ANSIBLE_CHATBOT_VERSION=latest
ARG IMAGE_TAGS=image-tags-not-defined
ARG GIT_COMMIT=git-commit-not-defined

# ======================================================
# Base-image with all dependencies for Python venv
# Lightspeed-stack bundles all required dependencies
# For running llama-stack in library mode.
#
# To include more dependencies, create upstream PR
# to update this file:
# https://github.com/lightspeed-core/lightspeed-stack/blob/main/pyproject.toml
# ------------------------------------------------------
FROM quay.io/lightspeed-core/lightspeed-stack:0.2.0

ARG APP_ROOT=/app-root
WORKDIR /app-root

# Add only project-specific dependencies without adding other dependencies
# to not break the dependencies of the base image.
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_CACHE=1
RUN uv pip install . --no-deps && uv clean

USER 0

RUN microdnf install -y --nodocs --setopt=keepcache=0 --setopt=tsflags=nodocs jq

# Add explicit files and directories
# (avoid accidental inclusion of local directories or env files or credentials)
COPY LICENSE.md README.md ./

# this directory is checked by ecosystem-cert-preflight-checks task in Konflux
COPY LICENSE.md /licenses/

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
