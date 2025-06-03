ARG ANSIBLE_CHATBOT_BASE_IMAGE=ansible-chatbot-stack-base
ARG LLAMA_STACK_VERSION=0.2.9
FROM ${ANSIBLE_CHATBOT_BASE_IMAGE}:${LLAMA_STACK_VERSION}

RUN mkdir -p /.llama/distributions/ansible-chatbot
ADD ansible-chatbot-run.yaml /.llama/distributions/ansible-chatbot

ENTRYPOINT ["python", "-m", "llama_stack.distribution.server.server", "--config", "/.llama/distributions/ansible-chatbot/ansible-chatbot-run.yaml"]