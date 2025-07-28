#!/bin/bash
MOUNTPATH=/.llama/data

echo "Checking preloaded embedding model..."
if [[ -e /.llama/data/distributions/ansible-chatbot/embeddings_model ]]; then
  echo "/.llama/data/distributions/ansible-chatbot/embeddings_model already exists."
else
  if [[ ! -d ${MOUNTPATH} ]]; then
    echo "Volume mount path is not found."
    exit 1
  else
    if [[ ! -d ${MOUNTPATH}/embeddings_model ]]; then
      echo "Embedding model is not found on the volume mount path."
      exit 1
    else
      ln -s ${MOUNTPATH}/embeddings_model /.llama/data/distributions/ansible-chatbot/embeddings_model
      if [[ $? != 0 ]]; then
        echo "Failed to create symlink ./embeddings_model"
        exit 1
      fi
      echo "Symlink /.llama/data/distributions/ansible-chatbot/embeddings_model has been created."
    fi
  fi
fi

python3.12 /app-root/src/lightspeed_stack.py --config /.llama/distributions/ansible-chatbot/config/lightspeed-stack.yaml
