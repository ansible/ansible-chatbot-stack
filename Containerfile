ARG LLAMA_STACK_VERSION=0.2.7
FROM ansible-chatbot:${LLAMA_STACK_VERSION}

RUN mkdir -p /.llama/distributions/ansible-chatbot
ADD aap_faiss_store.db /.llama/distributions/ansible-chatbot
ADD ansible-chatbot-run.yaml /.llama/distributions/ansible-chatbot
