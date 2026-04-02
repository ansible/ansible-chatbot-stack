#!/bin/bash
set -o pipefail

MOUNTPATH=/.llama/data

VECTOR_DB_PATH="/.llama/data/distributions/ansible-chatbot"
FAISS_STORE_DB_FILE="aap_faiss_store.db"
PROVIDER_VECTOR_DB_ID_FILE="provider_vector_db_id.ind"

BYOK_VECTOR_DB_PATH="/.llama/data/byok/distributions/ansible-chatbot"
BYOK_FAISS_STORE_DB_FILE_PATH="${BYOK_VECTOR_DB_PATH}/faiss_store.db"
BYOK_PROVIDER_VECTOR_DB_ID_FILE_PATH="${BYOK_VECTOR_DB_PATH}/provider_vector_db_id.ind"

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

# log vector db files
FAISS_STORE_DB_FILE_PATH="${VECTOR_DB_PATH}/${FAISS_STORE_DB_FILE}"
PROVIDER_VECTOR_DB_ID_FILE_PATH="${VECTOR_DB_PATH}/${PROVIDER_VECTOR_DB_ID_FILE}"
echo "Checking store DB files..."
if [[ -f "${FAISS_STORE_DB_FILE_PATH}" ]]; then
    FAISS_DB_SIZE=$(du -k "${FAISS_STORE_DB_FILE_PATH}" | awk '{printf "%.2f MB", $1/1024}')
    FAISS_DB_DATETIME=$(ls -lh "${FAISS_STORE_DB_FILE_PATH}" | awk '{print $6, $7, $8}')
    echo "FAISS DB file exists: ${FAISS_STORE_DB_FILE_PATH}"
    echo "FAISS DB file size: ${FAISS_DB_SIZE}"
    echo "FAISS DB file date/time: ${FAISS_DB_DATETIME}"
else
    echo "FAISS DB file not found: ${FAISS_STORE_DB_FILE_PATH}"
fi

# log and export vector DB ID if not already done
if [[ -f "${PROVIDER_VECTOR_DB_ID_FILE_PATH}" ]]; then
    PROVIDER_ID=$(cat ${PROVIDER_VECTOR_DB_ID_FILE_PATH})
    echo "Provider vector DB ID file exists: ${PROVIDER_VECTOR_DB_ID_FILE_PATH}"
    echo "Provider vector DB ID: ${PROVIDER_ID}"
    if [[ -z "${PROVIDER_VECTOR_DB_ID:-}" ]]; then
        export PROVIDER_VECTOR_DB_ID="${PROVIDER_ID}"
        echo "Exported PROVIDER_VECTOR_DB_ID: ${PROVIDER_VECTOR_DB_ID}"
    else
        echo "PROVIDER_VECTOR_DB_ID already set: ${PROVIDER_VECTOR_DB_ID}"
    fi
else
    echo "Provider vector DB ID file not found: ${PROVIDER_VECTOR_DB_ID_FILE_PATH}"
fi

# log BYOK image if supplied
if [[ -n "${BYOK_IMAGE:-}" ]]; then
    echo "BYOK_IMAGE is set: ${BYOK_IMAGE}"
else
    echo "BYOK_IMAGE is not set"
fi

# log BYOK vector db files
echo "Checking BYOK vector DB files..."
if [[ -f "${BYOK_FAISS_STORE_DB_FILE_PATH}" ]]; then
    BYOK_DB_SIZE=$(du -k "${BYOK_FAISS_STORE_DB_FILE_PATH}" | awk '{printf "%.2f MB", $1/1024}')
    BYOK_DB_DATETIME=$(ls -lh "${BYOK_FAISS_STORE_DB_FILE_PATH}" | awk '{print $6, $7, $8}')
    echo "BYOK FAISS DB file exists: ${BYOK_FAISS_STORE_DB_FILE_PATH}"
    echo "BYOK FAISS DB file size: ${BYOK_DB_SIZE}"
    echo "BYOK FAISS DB file date/time: ${BYOK_DB_DATETIME}"
else
    echo "BYOK FAISS DB file not found: ${BYOK_FAISS_STORE_DB_FILE_PATH}"
fi

# log and export BYOK  vector DB ID if not already done
if [[ -f "${BYOK_PROVIDER_VECTOR_DB_ID_FILE_PATH}" ]]; then
    BYOK_ID=$(cat ${BYOK_PROVIDER_VECTOR_DB_ID_FILE_PATH})
    echo "BYOK provider vector DB ID file exists: ${BYOK_PROVIDER_VECTOR_DB_ID_FILE_PATH}"
    echo "BYOK provider vector DB ID: ${BYOK_ID}"
    if [[ -z "${BYOK_PROVIDER_VECTOR_DB_ID:-}" ]]; then
        export BYOK_PROVIDER_VECTOR_DB_ID="${BYOK_ID}"
        echo "Exported BYOK_PROVIDER_VECTOR_DB_ID: ${BYOK_PROVIDER_VECTOR_DB_ID}"
    else
        echo "BYOK_PROVIDER_VECTOR_DB_ID already set: ${BYOK_PROVIDER_VECTOR_DB_ID}"
    fi
else
    echo "BYOK provider vector DB ID file not found: ${BYOK_PROVIDER_VECTOR_DB_ID_FILE_PATH}"
fi

${PYTHON_CMD} /app-root/src/lightspeed_stack.py --config /.llama/distributions/ansible-chatbot/config/lightspeed-stack.yaml
