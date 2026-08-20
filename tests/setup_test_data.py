#!/usr/bin/env python3
"""
Setup script to create test data for integration tests.

This script:
1. Downloads the public all-MiniLM-L6-v2 embeddings model from HuggingFace
2. Creates a minimal FAISS vector database in llama-stack's SQLite format

This allows tests to run without needing access to private container registries.
"""

import asyncio
import os
import sys
from pathlib import Path


# The Containerfile always runs the chatbot as this UID:GID (`RUN chown -R 1001:1001
# /.llama`, `USER 1001`), regardless of runtime or platform.
_CONTAINER_UID = 1001
_CONTAINER_GID = 1001


def _grant_container_access(path: Path, dir_mode: int, file_mode: int):
    """
    Make a host-created path accessible to the chatbot container.

    Chowning to the container's UID:GID lets us use restrictive permissions, but
    that only succeeds when the calling process already has that UID (true in CI,
    where the runner user is UID 1001) or is root. Locally the invoking user is
    usually a different UID and can't chown to 1001 without privilege, so fall
    back to world-accessible permissions there instead of failing setup.
    """
    mode = dir_mode if path.is_dir() else file_mode
    try:
        os.chown(path, _CONTAINER_UID, _CONTAINER_GID)
        path.chmod(mode)
    except PermissionError:
        path.chmod(0o777 if path.is_dir() else 0o666)


def setup_embeddings_model(target_dir: Path):
    """Download the public embeddings model from HuggingFace."""
    import shutil
    from sentence_transformers import SentenceTransformer

    print(f"📦 Downloading embeddings model...")

    model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

    # Wipe any existing directory first: model.save() does not clear stale
    # files, so a leftover file from a previous model revision (e.g. an old
    # vocab.txt paired with a newer model.safetensors) can silently persist
    # and produce a tokenizer/model vocab mismatch at inference time.
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    model.save(str(target_dir))

    # SentenceTransformer.save() can leave weight files (e.g. model.safetensors)
    # with the restrictive mode HuggingFace's cache stores them under (owner-only),
    # while the chatbot container reads this directory as a different, non-matching
    # UID. This directory is read-only from the container's perspective, so world
    # read/traverse (not write) is enough for it to load.
    for root, dirs, files in os.walk(target_dir):
        for d in dirs:
            (Path(root) / d).chmod(0o755)
        for f in files:
            (Path(root) / f).chmod(0o644)

    print(f"✅ Embeddings model saved")
    return model


async def _write_faiss_vector_db(db_path: Path, model, vector_store_id: str, documents: list, provider_id: str):
    """
    Populate a kvstore SQLite file using llama-stack's own FaissIndex/SqliteKVStoreImpl
    classes, rather than hand-rolling the key/value schema.

    A previous version of this script wrote its own key names (e.g. "<id>:faiss_index")
    with pickled Python dicts. That schema doesn't match what llama-stack's inline::faiss
    provider actually reads (keys prefixed "vector_stores:v3::" / "faiss_index:v3::",
    values that are VectorStore/EmbeddedChunk JSON) — so the fixture data was silently
    never loaded at query time. Driving the real classes guarantees the on-disk format
    always matches whatever llama-stack version is installed.
    """
    from llama_stack.core.storage.datatypes import SqliteKVStoreConfig
    from llama_stack.core.storage.kvstore.sqlite.sqlite import SqliteKVStoreImpl
    from llama_stack.providers.inline.vector_io.faiss.faiss import VECTOR_DBS_PREFIX, FaissIndex
    from llama_stack_api import ChunkMetadata, EmbeddedChunk, VectorStore

    if db_path.exists():
        db_path.unlink()

    # Matches the embedding_model string the run YAMLs register for this same
    # sentence-transformers model (e.g. openai-chatbot-run.yaml's vector_stores
    # entry), so a pre-registered record here is indistinguishable from one the
    # container registers itself at startup.
    embedding_model = f"sentence-transformers/{os.environ.get('EMBEDDINGS_MODEL', '/.llama/data/embeddings_model')}"

    texts = [doc["content"] for doc in documents]
    embeddings = model.encode(texts)
    dimension = int(embeddings.shape[1])

    kvstore = SqliteKVStoreImpl(SqliteKVStoreConfig(db_path=str(db_path)))
    await kvstore.initialize()

    # Pre-register the vector store itself. The chatbot container also (re-)registers
    # this from its own YAML config at startup, but writing it here means the fixture
    # is immediately self-contained and loadable without relying on that timing.
    vector_store = VectorStore(
        identifier=vector_store_id,
        provider_id=provider_id,
        provider_resource_id=vector_store_id,
        embedding_model=embedding_model,
        embedding_dimension=dimension,
    )
    await kvstore.set(
        key=f"{VECTOR_DBS_PREFIX}{vector_store_id}",
        value=vector_store.model_dump_json(),
    )

    index = await FaissIndex.create(dimension, kvstore, vector_store_id)
    embedded_chunks = [
        EmbeddedChunk(
            content=doc["content"],
            chunk_id=f"{vector_store_id}-chunk-{i}",
            metadata=doc["metadata"],
            chunk_metadata=ChunkMetadata(document_id=doc["metadata"].get("document_id", vector_store_id)),
            embedding=[float(x) for x in embeddings[i]],
            embedding_model=embedding_model,
            embedding_dimension=dimension,
        )
        for i, doc in enumerate(documents)
    ]
    await index.add_chunks(embedded_chunks)
    return dimension


def setup_vector_db(target_dir: Path, model, provider_id: str):
    """
    Create a minimal FAISS vector database in llama-stack's SQLite kvstore format.

    llama-stack's inline::faiss provider uses SQLite to store:
    - The FAISS index (serialized)
    - Document metadata and chunks
    """
    print(f"📦 Creating vector database in {target_dir}...")

    # Dummy AAP documentation content
    # document_id is required: llama-stack's citation-building code keys a
    # dict by chunk.metadata["document_id"], and a missing key resolves to
    # None, producing an invalid {None: filename} dict that fails Pydantic
    # validation on ToolExecutionResult.citation_files and 500s the request.
    documents = [
        {
            "content": "AAP stands for Ansible Automation Platform. It is a comprehensive enterprise automation solution by Red Hat.",
            "metadata": {"source": "test", "document_id": "test-doc-0", "chunk_id": "0"}
        },
        {
            "content": "Ansible Automation Platform provides automation capabilities for IT operations, cloud provisioning, and configuration management.",
            "metadata": {"source": "test", "document_id": "test-doc-1", "chunk_id": "1"}
        },
        {
            "content": "Key components of AAP include Automation Controller, Automation Hub, and Event-Driven Ansible.",
            "metadata": {"source": "test", "document_id": "test-doc-2", "chunk_id": "2"}
        },
        {
            "content": "Ansible EDA (Event-Driven Ansible) enables automated responses to events from various IT sources.",
            "metadata": {"source": "test", "document_id": "test-doc-3", "chunk_id": "3"}
        },
        {
            "content": "Automation Controller is the web UI and API for managing Ansible automation at scale.",
            "metadata": {"source": "test", "document_id": "test-doc-4", "chunk_id": "4"}
        },
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = target_dir / "aap_faiss_store.db"

    print("  Creating embeddings for dummy documents...")
    dimension = asyncio.run(
        _write_faiss_vector_db(db_path, model, vector_store_id=provider_id, documents=documents, provider_id="aap_faiss")
    )

    # llama-stack's FAISS provider writes to the kvstore at startup (vector-store
    # registration). SQLite also needs to create a rollback-journal file next to
    # the .db file for every write transaction, so the *directory* must be
    # writable too, not just the .db file itself — otherwise both fail with
    # "attempt to write a readonly database".
    _grant_container_access(db_path, dir_mode=0o750, file_mode=0o640)
    _grant_container_access(target_dir, dir_mode=0o750, file_mode=0o640)

    # Create provider ID file
    (target_dir / "provider_vector_db_id.ind").write_text(provider_id)

    print(f"✅ Vector database created: {db_path}")
    print(f"   - {len(documents)} documents indexed")
    print(f"   - Embedding dimension: {dimension}")

    return provider_id


def setup_byok_vector_db(target_dir: Path, model, vector_store_id: str):
    """
    Create a minimal BYOK FAISS vector database for sanity testing.

    Uses identical SQLite kvstore format as setup_vector_db() but with
    distinctive content that the standard AAP docs do not contain, so
    test_byok_vector_db_retrieval can verify BYOK retrieval specifically.
    """
    print(f"📦 Creating BYOK vector database in {target_dir}...")

    # A single chunk, not three: "What is AnsibleByokPlugin?" retrieves whichever
    # chunk scores highest, and a plain definitional sentence alone consistently
    # outranks the ones carrying the distinctive facts (version, capabilities) that
    # test_byok_vector_db_retrieval checks for. Keeping all of it in one chunk means
    # retrieval doesn't depend on top-k/relevance-cutoff luck to surface those facts.
    documents = [
        {
            "content": (
                "AnsibleByokPlugin is a fictional automation plugin used exclusively "
                "for BYOK sanity testing. It integrates custom knowledge sources into "
                "ansible-chatbot-stack via BYOK. AnsibleByokPlugin version 1.0 supports "
                "real-time event processing and dynamic knowledge retrieval."
            ),
            "metadata": {"source": "byok-test", "document_id": "byok-test-doc-0", "chunk_id": "0"}
        },
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = target_dir / "faiss_store.db"

    # provider_id must be "byok-docs" to match the rag_id declared for the
    # inline::faiss BYOK provider in byok-lightspeed-stack.yaml.
    dimension = asyncio.run(
        _write_faiss_vector_db(db_path, model, vector_store_id, documents, provider_id="byok-docs")
    )

    # See setup_vector_db()'s matching _grant_container_access calls: the container
    # needs write access to this host-created file, and to the containing directory
    # (SQLite creates a rollback-journal file there on every write transaction).
    _grant_container_access(db_path, dir_mode=0o750, file_mode=0o640)
    _grant_container_access(target_dir, dir_mode=0o750, file_mode=0o640)

    (target_dir / "provider_vector_db_id.ind").write_text(vector_store_id)

    print(f"✅ BYOK vector database created: {db_path}")
    print(f"   - {len(documents)} documents indexed")
    print(f"   - Embedding dimension: {dimension}")
    print(f"   - Vector store ID: {vector_store_id}")

    return vector_store_id


def main():
    """Main entry point."""
    # Determine project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # TEST_DATA_ROOT lets the sanity test suite generate its own copy of the
    # fixtures under .test_data/, kept separate from the ones used for local
    # development and the mock test suite (tests/conftest.py). A relative value
    # is resolved against the project root, not the caller's cwd.
    test_data_root = os.environ.get("TEST_DATA_ROOT")
    if test_data_root:
        data_root = Path(test_data_root)
        if not data_root.is_absolute():
            data_root = project_root / data_root
    else:
        data_root = project_root

    embeddings_dir = data_root / "embeddings_model"
    vector_db_dir = data_root / "vector_db"
    byok_vector_db_dir = data_root / "byok_vector_db"

    # Provider ID that matches the test config
    provider_id = "aap-product-docs-2_6"
    byok_vector_store_id = "byok-sanity-test-0001"

    # Check if already set up
    if embeddings_dir.exists() and vector_db_dir.exists() and byok_vector_db_dir.exists():
        print("✅ Test data already exists. Use --force to recreate.")
        if "--force" not in sys.argv:
            return 0
        print("🔄 Recreating test data...")

    # Setup embeddings model
    model = setup_embeddings_model(embeddings_dir)

    # Setup vector database
    setup_vector_db(vector_db_dir, model, provider_id)

    # Setup BYOK vector database
    setup_byok_vector_db(byok_vector_db_dir, model, byok_vector_store_id)

    print("\n✅ Test data setup complete!")
    print(f"   Embeddings model: {embeddings_dir}")
    print(f"   Vector database: {vector_db_dir}")
    print(f"   BYOK vector database: {byok_vector_db_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
