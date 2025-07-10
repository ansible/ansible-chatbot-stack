# Makefile for Ansible Chatbot Stack

# Default values for environment variables
QUAY_ORG ?=
ANSIBLE_CHATBOT_VERSION ?=
ANSIBLE_CHATBOT_VLLM_URL ?=
ANSIBLE_CHATBOT_VLLM_API_TOKEN ?=
ANSIBLE_CHATBOT_INFERENCE_MODEL ?=
ANSIBLE_CHATBOT_INFERENCE_MODEL_FILTER ?=
LLAMA_STACK_PORT ?= 8321
LOCAL_DB_PATH ?= .
CONTAINER_DB_PATH ?= /.llama/data/distributions/ansible-chatbot
# quay.io/ansible/aap-rag-content:latest does not work with lightspeed-stack:latest
# aap-rag-content uses llama-stack:0.2.14 whereas lightspeed-stack:latest uses 0.2.13.
RAG_CONTENT_IMAGE ?= quay.io/ansible/aap-rag-content:1.0.1751985495
# Colors for terminal output
RED := \033[0;31m
NC := \033[0m # No Color

.PHONY: help setup build build-custom run clean all deploy-k8s shell tag-and-push

.EXPORT_ALL_VARIABLES:

UV_HTTP_TIMEOUT=120

help:
	@echo "Makefile for Ansible Chatbot Stack"
	@echo "Available targets:"
	@echo "  help              - Show this help message"
	@echo "  all               - Run all steps (setup, build, build-custom)"
	@echo "  setup             - Sets up llama-stack and the external lightspeed providers"
	@echo "  setup-vector-db   - Sets up vector DB and embedding model"
	@echo "  build             - Build the customized Ansible Chatbot Stack image from lightspeed-core/lightspeed-stack"
	@echo "  run               - Run the Ansible Chatbot Stack container built with 'build-lsc'"
	@echo "  run-test          - Run some sanity checks for the  Ansible Chatbot Stack container built with 'build-lsc'"
	@echo "  run-local-db      - Run the Ansible Chatbot Stack container with local DB mapped to conatiner DB"
	@echo "  clean             - Clean up generated files and Docker images"
	@echo "  deploy-k8s        - Deploy to Kubernetes cluster"
	@echo "  shell             - Get a shell in the container"
	@echo "  tag-and-push      - Tag and push the container image to quay.io"
	@echo ""
	@echo "Required Environment variables:"
	@echo "  ANSIBLE_CHATBOT_VERSION                - Version tag for the image (default: $(ANSIBLE_CHATBOT_VERSION))"
	@echo "  ANSIBLE_CHATBOT_VLLM_URL               - URL for the vLLM inference provider"
	@echo "  ANSIBLE_CHATBOT_VLLM_API_TOKEN         - API token for the vLLM inference provider"
	@echo "  ANSIBLE_CHATBOT_INFERENCE_MODEL        - Inference model to use"
	@echo "  ANSIBLE_CHATBOT_INFERENCE_MODEL_FILTER	- Inference model to use for tools filtering"
	@echo "  CONTAINER_DB_PATH                      - Path to the container database (default: $(CONTAINER_DB_PATH))"
	@echo "  LOCAL_DB_PATH                          - Path to the local database (default: $(LOCAL_DB_PATH))"
	@echo "  LLAMA_STACK_PORT                       - Port to expose (default: $(LLAMA_STACK_PORT))"
	@echo "  QUAY_ORG                               - Quay organization name (default: $(QUAY_ORG))"

setup: setup-vector-db
	@echo "Setting up environment..."
	uv sync
	mkdir -p llama-stack/providers.d/inline/agents/
	curl -o llama-stack/providers.d/inline/agents/lightspeed_inline_agent.yaml https://raw.githubusercontent.com/lightspeed-core/lightspeed-providers/refs/heads/main/resources/external_providers/inline/agents/lightspeed_inline_agent.yaml
	@echo "Environment setup complete."

setup-vector-db:
	@echo "Setting up vector db and embedding image..."
	rm -rf ./vector_db ./embeddings_model
	mkdir -p ./vector_db
	docker run -d --rm --name rag-content $(RAG_CONTENT_IMAGE) sleep infinity
	docker cp rag-content:/rag/llama_stack_vector_db/faiss_store.db.gz ./vector_db/aap_faiss_store.db.gz
	docker cp rag-content:/rag/embeddings_model .
	docker kill rag-content
	gzip -d ./vector_db/aap_faiss_store.db.gz

# Pre-check required environment variables for build
check-env-build:
	@if [ -z "$(ANSIBLE_CHATBOT_VERSION)" ]; then \
		printf "$(RED)Error: ANSIBLE_CHATBOT_VERSION is required but not set$(NC)\n"; \
		exit 1; \
	fi

build: check-env-build
	@echo "Building customized Ansible Chatbot Stack image from lightspeed-core/lightspeed-stack..."
	docker build -f ./Containerfile \
		--build-arg ANSIBLE_CHATBOT_VERSION=$(ANSIBLE_CHATBOT_VERSION) \
		-t ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION) .
	@printf "Custom image $(RED)ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)$(NC) built successfully.\n"

# Pre-check for required environment variables
check-env-run:
	@if [ -z "$(ANSIBLE_CHATBOT_VLLM_URL)" ]; then \
		printf "$(RED)Error: ANSIBLE_CHATBOT_VLLM_URL is required but not set$(NC)\n"; \
		exit 1; \
	fi
	@if [ -z "$(ANSIBLE_CHATBOT_VLLM_API_TOKEN)" ]; then \
		printf "$(RED)Error: ANSIBLE_CHATBOT_VLLM_API_TOKEN is required but not set$(NC)\n"; \
		exit 1; \
	fi
	@if [ -z "$(ANSIBLE_CHATBOT_INFERENCE_MODEL)" ]; then \
		printf "$(RED)Error: ANSIBLE_CHATBOT_INFERENCE_MODEL is required but not set$(NC)\n"; \
		exit 1; \
	fi
	@if [ -z "$(ANSIBLE_CHATBOT_VERSION)" ]; then \
		printf "$(RED)Error: ANSIBLE_CHATBOT_VERSION is required but not set$(NC)\n"; \
		exit 1; \
	fi

run: check-env-run
	@echo "Running Ansible Chatbot Stack container..."
	@echo "Using vLLM URL: $(ANSIBLE_CHATBOT_VLLM_URL)"
	@echo "Using inference model: $(ANSIBLE_CHATBOT_INFERENCE_MODEL)"
	docker run --security-opt label=disable -it -p $(LLAMA_STACK_PORT):8080 \
	  -v ./embeddings_model:/.llama/data/embeddings_model \
	  -v ./vector_db/aap_faiss_store.db:$(CONTAINER_DB_PATH)/aap_faiss_store.db \
	  -v ./lightspeed-stack.yaml:/.llama/data/lightspeed-stack.yaml \
	  --env VLLM_URL=$(ANSIBLE_CHATBOT_VLLM_URL) \
	  --env VLLM_API_TOKEN=$(ANSIBLE_CHATBOT_VLLM_API_TOKEN) \
	  --env INFERENCE_MODEL=$(ANSIBLE_CHATBOT_INFERENCE_MODEL) \
	  --env INFERENCE_MODEL_FILTER=$(ANSIBLE_CHATBOT_INFERENCE_MODEL_FILTER) \
	  ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)

run-test:
	@echo "Running test query against lightspeed-core/lightspeed-stack's /config endpoint..."
	curl -X GET http://localhost:$(LLAMA_STACK_PORT)/v1/config | jq .
	@echo "Running test query against lightspeed-core/lightspeed-stack's /models endpoint..."
	curl -X GET http://localhost:$(LLAMA_STACK_PORT)/v1/models | jq .
	@echo "Running test query against lightspeed-core/lightspeed-stack's /query endpoint..."
	curl -X POST http://localhost:$(LLAMA_STACK_PORT)/v1/query -H "Content-Type: application/json" --data '{"query": "What is Ansible EDA?"}' | jq .

# Pre-check required environment variables for local DB run
check-env-run-local-db: check-env-run
	@if [ -z "$(LOCAL_DB_PATH)" ]; then \
		printf "$(RED)Error: LOCAL_DB_PATH is required but not set$(NC)\n"; \
		exit 1; \
	fi
	@if [ -z "$(CONTAINER_DB_PATH)" ]; then \
		printf "$(RED)Error: CONTAINER_DB_PATH is required but not set$(NC)\n"; \
		exit 1; \
	fi

run-local-db: check-env-run-local-db
	@echo "Running Ansible Chatbot Stack container..."
	@echo "Using vLLM URL: $(ANSIBLE_CHATBOT_VLLM_URL)"
	@echo "Using inference model: $(ANSIBLE_CHATBOT_INFERENCE_MODEL)"
	@echo "Using inference model for tools filtering : $(ANSIBLE_CHATBOT_INFERENCE_MODEL_FILTER)"
	@echo "Mapping local DB from $(LOCAL_DB_PATH) to $(CONTAINER_DB_PATH)"
	docker run --security-opt label=disable -it -p $(LLAMA_STACK_PORT):8080 \
	  -v $(LOCAL_DB_PATH):$(CONTAINER_DB_PATH) \
	  -v ./embeddings_model:/app/embeddings_model \
	  -v ./vector_db/aap_faiss_store.db:$(CONTAINER_DB_PATH)/aap_faiss_store.db \
	  -v ./lightspeed-stack.yaml:/.llama/data/lightspeed-stack.yaml \
	  --env VLLM_URL=$(ANSIBLE_CHATBOT_VLLM_URL) \
	  --env VLLM_API_TOKEN=$(ANSIBLE_CHATBOT_VLLM_API_TOKEN) \
	  --env INFERENCE_MODEL=$(ANSIBLE_CHATBOT_INFERENCE_MODEL) \
	  --env INFERENCE_MODEL_FILTER=$(ANSIBLE_CHATBOT_INFERENCE_MODEL_FILTER) \
	  ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)

clean:
	@echo "Cleaning up..."
	@echo "Cleaning up your local folders..."
	rm -rf llama-stack/
	rm -rf providers.d/
	@echo "Removing ansible-chatbot-stack images..."
	docker rmi -f $$(docker images -a -q --filter reference=ansible-chatbot-stack) || true
	@echo "Removing ansible-chatbot-stack containers..."
	docker rm -f $$(docker ps -a -q --filter ancestor=ansible-chatbot-stack) || true
	@echo "Clean-up complete."

deploy-k8s:
	@echo Change configuration in `kustomization.yaml` accordingly, then deploy
	kubectl kustomize . > local-chatbot-stack-deploy.yaml
	@echo Deploy the service:
	kubectl apply -f local-chatbot-stack-deploy.yaml
	@echo "Deployment initiated. Verify using kubectl commands."

shell:
	@echo "Getting a shell in the container..."
	docker run --security-opt label=disable -it --entrypoint /bin/bash ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)

# Pre-check required environment variables for tag-and-push
check-env-tag-and-push:
	@if [ -z "$(QUAY_ORG)" ]; then \
		printf "$(RED)Error: QUAY_ORG is required but not set$(NC)\n"; \
		exit 1; \
	fi
	@if [ -z "$(ANSIBLE_CHATBOT_VERSION)" ]; then \
		printf "$(RED)Error: ANSIBLE_CHATBOT_VERSION is required but not set$(NC)\n"; \
		exit 1; \
	fi

tag-and-push: check-env-tag-and-push
	@echo "Logging in to quay.io..."
	@echo "Please enter your quay.io credentials when prompted"
	docker login quay.io
	@echo "Tagging image ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)"
	docker tag ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION) quay.io/$(QUAY_ORG)/ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)
	@echo "Pushing image to quay.io..."
	docker push quay.io/$(QUAY_ORG)/ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)
	@echo "Image successfully pushed to quay.io/$(QUAY_ORG)/ansible-chatbot-stack:$(ANSIBLE_CHATBOT_VERSION)"

all: setup build build-custom
	@echo "All build steps completed successfully."
	@printf "To run the container, use: $(RED)make run$(NC)\n"
	@printf "To tag and push the container to quay.io, use: $(RED)make tag-and-push$(NC)\n"
