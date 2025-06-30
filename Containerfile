# Build arguments declared in the global scope.
ARG ANSIBLE_CHATBOT_BASE_IMAGE=ansible-chatbot-stack-base
ARG ANSIBLE_CHATBOT_VERSION=latest
ARG LLAMA_STACK_VERSION=0.2.9

FROM ${ANSIBLE_CHATBOT_BASE_IMAGE}:${LLAMA_STACK_VERSION}

# Re-declaring arguments without a value, inherits the global default one.
ARG ANSIBLE_CHATBOT_VERSION
ENV LLAMA_STACK_CONFIG_DIR=/.llama/data

# Data and configuration
RUN mkdir -p /.llama/distributions/ansible-chatbot
RUN mkdir -p /.llama/data/distributions/ansible-chatbot
ADD ansible-chatbot-run.yaml /.llama/distributions/ansible-chatbot
RUN echo -e "\
{\n\
  \"version\": \"${ANSIBLE_CHATBOT_VERSION}\" \n\
}\n\
" > /.llama/distributions/ansible-chatbot/ansible-chatbot-version-info.json
RUN chmod -R g+rw /.llama

# Bootstrap
RUN mkdir -p /.llama/temp
ADD entrypoint.sh /.llama/temp
RUN chmod +x /.llama/temp/entrypoint.sh

# See https://github.com/meta-llama/llama-stack/issues/1633
# USER 1000

ENTRYPOINT ["/.llama/temp/entrypoint.sh"]
