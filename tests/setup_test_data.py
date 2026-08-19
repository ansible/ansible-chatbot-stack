#!/usr/bin/env python3
"""
Setup script to create test data for integration tests.

This script:
1. Downloads the public all-MiniLM-L6-v2 embeddings model from HuggingFace
2. Creates a minimal FAISS vector database in llama-stack's SQLite format

This allows tests to run without needing access to private container registries.
"""

import os
import sys
import json
import sqlite3
import pickle
from pathlib import Path


def setup_embeddings_model(target_dir: Path):
    """Download the public embeddings model from HuggingFace."""
    from sentence_transformers import SentenceTransformer
    
    print(f"📦 Downloading embeddings model...")
    
    model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    
    # Save to target directory
    target_dir.mkdir(parents=True, exist_ok=True)
    model.save(str(target_dir))
    
    print(f"✅ Embeddings model saved")
    return model


def setup_vector_db(target_dir: Path, model, provider_id: str):
    """
    Create a minimal FAISS vector database in llama-stack's SQLite kvstore format.
    
    llama-stack's inline::faiss provider uses SQLite to store:
    - The FAISS index (serialized)
    - Document metadata and chunks
    """
    import faiss
    import numpy as np
    
    print(f"📦 Creating vector database in {target_dir}...")
    
    # Dummy AAP documentation content
    documents = [
        {
            "content": "AAP stands for Ansible Automation Platform. It is a comprehensive enterprise automation solution by Red Hat.",
            "metadata": {"source": "test", "chunk_id": "0"}
        },
        {
            "content": "Ansible Automation Platform provides automation capabilities for IT operations, cloud provisioning, and configuration management.",
            "metadata": {"source": "test", "chunk_id": "1"}
        },
        {
            "content": "Key components of AAP include Automation Controller, Automation Hub, and Event-Driven Ansible.",
            "metadata": {"source": "test", "chunk_id": "2"}
        },
        {
            "content": "Ansible EDA (Event-Driven Ansible) enables automated responses to events from various IT sources.",
            "metadata": {"source": "test", "chunk_id": "3"}
        },
        {
            "content": "Automation Controller is the web UI and API for managing Ansible automation at scale.",
            "metadata": {"source": "test", "chunk_id": "4"}
        },
    ]
    
    # Create embeddings
    print("  Creating embeddings for dummy documents...")
    texts = [doc["content"] for doc in documents]
    embeddings = model.encode(texts)
    
    # Create FAISS index
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings.astype(np.float32))
    
    # Serialize FAISS index
    faiss_bytes = faiss.serialize_index(index)
    
    # Create SQLite database in llama-stack's format
    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = target_dir / "aap_faiss_store.db"
    
    # Remove existing database
    if db_path.exists():
        db_path.unlink()
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create the kvstore table that llama-stack expects
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kvstore (
            key TEXT PRIMARY KEY,
            value BLOB
        )
    """)
    
    # Store the FAISS index
    cursor.execute(
        "INSERT OR REPLACE INTO kvstore (key, value) VALUES (?, ?)",
        (f"{provider_id}:faiss_index", faiss_bytes.tobytes())
    )
    
    # Store document chunks with their metadata
    for i, doc in enumerate(documents):
        chunk_key = f"{provider_id}:chunk:{i}"
        chunk_data = {
            "content": doc["content"],
            "metadata": doc["metadata"],
            "embedding_index": i
        }
        cursor.execute(
            "INSERT OR REPLACE INTO kvstore (key, value) VALUES (?, ?)",
            (chunk_key, pickle.dumps(chunk_data))
        )
    
    # Store metadata about the vector DB
    db_metadata = {
        "num_chunks": len(documents),
        "dimension": dimension,
        "provider_id": provider_id
    }
    cursor.execute(
        "INSERT OR REPLACE INTO kvstore (key, value) VALUES (?, ?)",
        (f"{provider_id}:metadata", pickle.dumps(db_metadata))
    )
    
    conn.commit()
    conn.close()
    
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
    import faiss
    import numpy as np

    print(f"📦 Creating BYOK vector database in {target_dir}...")

    documents = [
        {
            "content": "AnsibleByokPlugin is a fictional automation plugin used exclusively for BYOK sanity testing.",
            "metadata": {"source": "byok-test", "chunk_id": "0"}
        },
        {
            "content": "The AnsibleByokPlugin integrates custom knowledge sources into ansible-chatbot-stack via BYOK.",
            "metadata": {"source": "byok-test", "chunk_id": "1"}
        },
        {
            "content": "AnsibleByokPlugin version 1.0 supports real-time event processing and dynamic knowledge retrieval.",
            "metadata": {"source": "byok-test", "chunk_id": "2"}
        },
    ]

    texts = [doc["content"] for doc in documents]
    embeddings = model.encode(texts)

    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings.astype(np.float32))

    faiss_bytes = faiss.serialize_index(index)

    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = target_dir / "faiss_store.db"

    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kvstore (
                key TEXT PRIMARY KEY,
                value BLOB
            )
        """)

        cursor.execute(
            "INSERT OR REPLACE INTO kvstore (key, value) VALUES (?, ?)",
            (f"{vector_store_id}:faiss_index", faiss_bytes.tobytes())
        )

        for i, doc in enumerate(documents):
            chunk_key = f"{vector_store_id}:chunk:{i}"
            chunk_data = {
                "content": doc["content"],
                "metadata": doc["metadata"],
                "embedding_index": i
            }
            cursor.execute(
                "INSERT OR REPLACE INTO kvstore (key, value) VALUES (?, ?)",
                (chunk_key, pickle.dumps(chunk_data))
            )

        db_metadata = {
            "num_chunks": len(documents),
            "dimension": dimension,
            "provider_id": vector_store_id
        }
        cursor.execute(
            "INSERT OR REPLACE INTO kvstore (key, value) VALUES (?, ?)",
            (f"{vector_store_id}:metadata", pickle.dumps(db_metadata))
        )

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

    embeddings_dir = project_root / "embeddings_model"
    vector_db_dir = project_root / "vector_db"
    byok_vector_db_dir = project_root / "byok_vector_db"

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
