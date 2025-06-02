ARG LLAMA_STACK_VERSION=0.2.7
FROM ansible-chatbot:${LLAMA_STACK_VERSION}

RUN mkdir -p /.llama/distributions/ansible-chatbot
ADD ansible-chatbot-run.yaml /.llama/distributions/ansible-chatbot

# Temporary workaround for k8s deployments
# We mount a PVC at /.llama/data and so the above _copy_ is hidden, masked by the PVC mount
# When the container starts we copy this temp file to the PVC
RUN mkdir -p /.llama/temp
ADD bootstrap.sh /.llama/temp
RUN chmod +x /.llama/temp/bootstrap.sh
ADD aap_faiss_store.db /.llama/temp

ENTRYPOINT ["/bin/sh", "/.llama/temp/bootstrap.sh"]