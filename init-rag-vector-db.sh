SRC_DIR=/rag/llama_stack_vector_db
DEST_DIR=/.llama/data/distributions/ansible-chatbot

if [ ! -d ${SRC_DIR} ]; then
  echo "Source directory ${SRC_DIR} does not exist."
  exit 1
fi

if [ ! -d ${DEST_DIR} ]; then
  echo "Destination directory ${DEST_DIR} does not exist."
  exit 1
fi

diff ${SRC_DIR}/faiss_store.db.gz.sha256 ${DEST_DIR}/faiss_store.db.gz.sha256
if [ $? -ne 0 ]; then
  gzip -cd ${SRC_DIR}/faiss_store.db.gz > ${DEST_DIR}/faiss_store.db
  cp ${SRC_DIR}/faiss_store.db.gz.sha256 ${DEST_DIR}
  echo "Vector DB file successfully updated to the latest version."
else
  echo "Vector DB file is up-to-date with the latest version."
fi
