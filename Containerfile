# Build arguments declared in the global scope.
ARG ANSIBLE_CHATBOT_VERSION=latest

# ======================================================
# Transient image to construct Python venv
# ------------------------------------------------------
FROM quay.io/lightspeed-core/lightspeed-stack:dev-latest

ARG APP_ROOT=/app-root
WORKDIR /app-root

# UV_PYTHON_DOWNLOADS=0 : Disable Python interpreter downloads and use the system interpreter.
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Add explicit files and directories
# (avoid accidental inclusion of local directories or env files or credentials)
COPY pyproject.toml LICENSE.md README.md ./

RUN uv pip install .

# ======================================================
# Final image without uv package manager
# ------------------------------------------------------
FROM quay.io/lightspeed-core/lightspeed-stack:dev-latest

USER 0

# Re-declaring arguments without a value, inherits the global default one.
ARG APP_ROOT
ARG ANSIBLE_CHATBOT_VERSION

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
ADD lightspeed-stack.yaml /.llama/distributions/ansible-chatbot
ADD ansible-chatbot-run.yaml /.llama/distributions/ansible-chatbot
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
