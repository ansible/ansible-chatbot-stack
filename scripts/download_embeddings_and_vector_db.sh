#!/bin/bash
RAG_CONTENT_IMAGE=quay.io/ansible/aap-rag-content:latest
SQLITE_STORE_DIR=~/.llama/data/distributions/.venv

mkdir -p ${SQLITE_STORE_DIR}

RAG_CONTENT_IMAGE=quay.io/ansible/aap-rag-content:latest
docker pull ${RAG_CONTENT_IMAGE}
docker run -d --rm --name rag-content ${RAG_CONTENT_IMAGE} sleep infinity
docker cp rag-content:/rag/llama_stack_vector_db/faiss_store.db.gz aap_faiss_store.db.gz
docker cp rag-content:/rag/embeddings_model .
docker kill rag-content

gzip -d aap_faiss_store.db.gz
mv aap_faiss_store.db ${SQLITE_STORE_DIR}
