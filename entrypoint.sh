#!/bin/bash
MOUNTPATH=/.llama/data

VECTOR_DB_PATH="/.llama/data/distributions/ansible-chatbot"
FAISS_STORE_DB_FILE="aap_faiss_store.db"

PYTHON_CMD="$@"

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

# cleanup vector db directory if exists
if [ -d "$VECTOR_DB_PATH" ]; then
    # Loop through all .db files in vector db directory
    for db_file in "$VECTOR_DB_PATH"/*.db; do
        # if the matched db_file exists and its filename is not FAISS_STORE_DB_FILE, remove it
        if [ -e "$db_file" ] && [ "$(basename "$db_file")" != "$FAISS_STORE_DB_FILE" ]; then
            rm "$db_file"
        fi
    done
fi

${PYTHON_CMD} /app-root/src/lightspeed_stack.py --config /.llama/distributions/ansible-chatbot/config/lightspeed-stack.yaml
