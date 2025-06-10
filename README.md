# Ansible Chatbot (llama) Stack

An Ansible Chatbot (llama) Stack [custom distribution](https://llama-stack.readthedocs.io/en/latest/distributions/building_distro.html) (`Container` type).

It includes:

- A remote vLLM inference provider (RHOSAI vLLM compatible)
- The inline sentence transformers (Meta)
- AAP RAG database files and configuration
- [Lightspeed external providers](https://github.com/lightspeed-core/lightspeed-providers)
- Other default providers from the [Remote vLLM distribution](https://llama-stack.readthedocs.io/en/latest/distributions/self_hosted_distro/remote-vllm.html) as well

Build/Run overview:

```mermaid
flowchart TB
%% Nodes
    CHATBOT_STACK([fa:fa-layer-group ansible-chatbot-stack-base:x.y.z])
    AAP_CHATBOT_STACK([fa:fa-layer-group ansible-chatbot-stack:x.y.z])
    AAP_CHATBOT([fa:fa-comment Ansible Chatbot Service])
    CHATBOT_BUILD_CONFIG{{fa:fa-wrench ansible-chatbot-build.yaml}}
    CHATBOT_RUN_CONFIG{{fa:fa-wrench ansible-chatbot-run.yaml}}
    AAP_CHATBOT_DOCKERFILE{{fa:fa-wrench Containerfile}}
    Lightspeed_Providers("fa:fa-code-branch lightspeed-providers")
    PYPI("fa:fa-database PyPI")

%% Edge connections between nodes
    CHATBOT_STACK -- Consumes --> PYPI
    Lightspeed_Providers -- Publishes --> PYPI
    CHATBOT_STACK -- Built from --> CHATBOT_BUILD_CONFIG
    AAP_CHATBOT_STACK -- Built from --> AAP_CHATBOT_DOCKERFILE
    AAP_CHATBOT_STACK -- inherits from --> CHATBOT_STACK
    AAP_CHATBOT -- Uses --> CHATBOT_RUN_CONFIG
    AAP_CHATBOT_STACK -- Runtime --> AAP_CHATBOT
```

## Build

### Setup for Ansible Chatbot Stack

---

> Actually using temporary [lightspeed stack providers](https://pypi.org/project/lightspeed-stack-providers/) package, otherwise further need for [lightspeed external providers](https://github.com/lightspeed-core/lightspeed-providers) available on PyPI

- Install llama-stack on the host machine, if not present.
- External providers YAML manifests must be present in `providers.d/` of your host's llama-stack directory.
- External providers' python libraries must be in the container's python's library path, but also in the host machine's python library path. It is a workaround for [this hack](https://github.com/meta-llama/llama-stack/blob/0cc07311890c00feb5bbd40f5052c8a84a88aa65/llama_stack/cli/stack/_build.py#L299).

```shell
        make setup
```

### Building the Ansible Chatbot Stack

---

> Builds the image `ansible-chatbot-stack-base:$PYPI_VERSION`.

```shell
    make build
```

### Customizing the Ansible Chatbot Stack

---

> Builds the image `ansible-chatbot-stack:$ANSIBLE_CHATBOT_VERSION`.

```shell
    export ANSIBLE_CHATBOT_VERSION=0.0.1
    make build-custom
 ```

## Run

> Change the `ANSIBLE_CHATBOT_VERSION` version and inference parameters below accordingly.

```shell
    export ANSIBLE_CHATBOT_VERSION=0.0.1
    export ANSIBLE_CHATBOT_VLLM_URL=<YOUR_MODEL_SERVING_URL>
    export ANSIBLE_CHATBOT_VLLM_API_TOKEN=<YOUR_MODEL_SERVING_API_TOKEN>
    export ANSIBLE_CHATBOT_INFERENCE_MODEL=<YOUR_INFERENCE_MODEL>
    make run
```

## Deploy into a k8s cluster

### Change configuration in `kustomization.yaml` accordingly, then

```shell
    kubectl kustomize . > my-chatbot-stack-deploy.yaml
```

### Deploy the service

```shell
    kubectl apply -f my-chatbot-stack-deploy.yaml
```

### [Verify the deployment](https://llama-stack.readthedocs.io/en/latest/distributions/kubernetes_deployment.html#verifying-the-deployment)

## Appendix - Host clean-up

If you have the need for re-building images, apply the following clean-ups right before:

```shell
    make clean
```

## Appendix - Testing by using the CLI client

```shell
    > llama-stack-client --configure ...

    > llama-stack-client models list
    ┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    ┃ model_type             ┃ identifier                                       ┃ provider_resource_id                             ┃ metadata                                                       ┃ provider_id                                ┃
    ┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
    │ llm                    │ granite-3.3-8b-instruct                          │ granite-3.3-8b-instruct                          │                                                                │ rhosai_vllm_dev                            │
    ├────────────────────────┼──────────────────────────────────────────────────┼──────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────┼────────────────────────────────────────────┤
    │ embedding              │ all-MiniLM-L6-v2                                 │ all-MiniLM-L6-v2                                 │ {'embedding_dimension': 384.0}                                 │ inline_sentence-transformer                │
    └────────────────────────┴──────────────────────────────────────────────────┴──────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────┴────────────────────────────────────────────┘

    > llama-stack-client providers list
    ┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    ┃ API          ┃ Provider ID                  ┃ Provider Type                        ┃
    ┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
    │ inference    │ rhosai_vllm_dev              │ remote::vllm                         │
    │ inference    │ inline_sentence-transformer  │ inline::sentence-transformers        │
    │ vector_io    │ aap_faiss                    │ inline::faiss                        │
    │ safety       │ llama-guard                  │ inline::llama-guard                  │
    │ safety       │ lightspeed_question_validity │ inline::lightspeed_question_validity │
    │ agents       │ meta-reference               │ inline::meta-reference               │
    │ datasetio    │ localfs                      │ inline::localfs                      │
    │ telemetry    │ meta-reference               │ inline::meta-reference               │
    │ tool_runtime │ rag-runtime-0                │ inline::rag-runtime                  │
    │ tool_runtime │ model-context-protocol-1     │ remote::model-context-protocol       │
    │ tool_runtime │ lightspeed                   │ remote::lightspeed                   │
    └──────────────┴──────────────────────────────┴──────────────────────────────────────┘

    > llama-stack-client vector_dbs list
    ┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    ┃ identifier           ┃ provider_id ┃ provider_resource_id ┃ vector_db_type ┃ params                            ┃
    ┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
    │ aap-product-docs-2_5 │ aap_faiss   │ aap-product-docs-2_5 │                │ embedding_dimension: 384          │
    │                      │             │                      │                │ embedding_model: all-MiniLM-L6-v2 │
    │                      │             │                      │                │ type: vector_db                   │
    │                      │             │                      │                │                                   │
    └──────────────────────┴─────────────┴──────────────────────┴────────────────┴───────────────────────────────────┘

    > llama-stack-client inference chat-completion --message "tell me about Ansible Lightspeed"
    ...
```

## Appendix - Obtain a container shell

```shell
    # Obtain a container shell for the Ansible Chatbot Stack.
    make shell
```
