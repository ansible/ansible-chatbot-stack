ARG ANSIBLE_CHATBOT_BASE_IMAGE=ansible-chatbot-stack-base
ARG LLAMA_STACK_VERSION=0.2.9
FROM ${ANSIBLE_CHATBOT_BASE_IMAGE}:${LLAMA_STACK_VERSION}

RUN mkdir -p /.llama/distributions/ansible-chatbot
ADD ansible-chatbot-run.yaml /.llama/distributions/ansible-chatbot

RUN mkdir -p /.llama/temp
ADD entrypoint.sh /.llama/temp
RUN chmod +x /.llama/temp/entrypoint.sh

ENTRYPOINT ["/.llama/temp/entrypoint.sh"]