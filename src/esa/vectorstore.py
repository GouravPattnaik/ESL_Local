"""
vectorstore.py — Embedding generation and Vector index.

Supports two backends controlled by VECTOR_STORE in .env:
  - chromadb   : Local ChromaDB (great for local development, no AWS needed)
  - opensearch : AWS OpenSearch domain (for production / AWS deployment)

Architecture alignment:
  - Corresponds to 'Vector Store' step in the architecture.
  - Local: ChromaDB at CHROMA_DIR
  - AWS:   OpenSearch at OPENSEARCH_HOST

Input : list of text chunks (from documents.py)
Output: chunks indexed in vector store; supports similarity search and optional LLM answering.
"""

import logging
import os
from .config import setting

log = logging.getLogger(__name__)


# ── Embedding provider ────────────────────────────────────────────────────────

def _embedder():
    """
    Returns an embed function based on EMBEDDING_PROVIDER setting.
      - openai : OpenAI text-embedding-3-small (paid, set OPENAI_API_KEY)
      - local  : SentenceTransformers all-MiniLM-L6-v2 (free, offline)
    """
    provider = setting("EMBEDDING_PROVIDER", "local").lower()

    if provider == "openai":
        from openai import OpenAI
        model = setting("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        log.info("[VectorStore] Embedder: OpenAI (model=%s)", model)
        client = OpenAI(api_key=setting("OPENAI_API_KEY"))
        def embed(texts):
            response = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in response.data]
        return embed

    # --- Bedrock implementation (commented for later use once authorized) ---
    # if provider == "bedrock":
    #     import boto3
    #     model = setting("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v1")
    #     region = setting("AWS_REGION", "us-east-1")
    #     log.info("[VectorStore] Embedder: AWS Bedrock (model=%s, region=%s)", model, region)
    #     client = boto3.client("bedrock-runtime", region_name=region)
    #     import json as _json
    #     def embed(texts):
    #         vectors = []
    #         for text in texts:
    #             body = _json.dumps({"inputText": text})
    #             resp = client.invoke_model(modelId=model, body=body, contentType="application/json", accept="application/json")
    #             vectors.append(_json.loads(resp["body"].read())["embedding"])
    #         return vectors
    #     return embed

    # Fallback to local SentenceTransformers
    model_name = setting("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    log.info("[VectorStore] Embedder: Local SentenceTransformer (model=%s)", model_name)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    return lambda texts: model.encode(texts, normalize_embeddings=True).tolist()


# ── ChromaDB backend ──────────────────────────────────────────────────────────

def _get_chroma_collection():
    """Returns a ChromaDB persistent collection."""
    import chromadb
    chroma_dir = setting("CHROMA_DIR", "local_bucket/vectorstore/chroma")
    collection_name = setting("CHROMA_COLLECTION", "esa_documents")
    log.info("[VectorStore] ChromaDB path: %s | collection: %s", chroma_dir, collection_name)
    client = chromadb.PersistentClient(path=chroma_dir)
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"}
    )

def _index_chromadb(chunks: list[dict], vectors: list) -> int:
    col = _get_chroma_collection()
    col.upsert(
        ids=[c["id"] for c in chunks],
        embeddings=vectors,
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"], "chunk": c["chunk"]} for c in chunks],
    )
    log.info("[VectorStore] Output: %d chunks indexed in ChromaDB", len(chunks))
    return len(chunks)

def _search_chromadb(query_vector: list, k: int) -> list[dict]:
    col = _get_chroma_collection()
    results = col.query(query_embeddings=[query_vector], n_results=k)
    hits = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        hits.append({"text": doc, "metadata": meta})
    return hits


# ── OpenSearch backend ────────────────────────────────────────────────────────

def _get_opensearch_client():
    """Returns an IAM-authenticated OpenSearch client."""
    from opensearchpy import OpenSearch, RequestsHttpConnection
    try:
        from opensearchpy import AWSV4SignerAuth
        import boto3
        host = setting("OPENSEARCH_HOST", "")
        region = setting("AWS_REGION", "us-east-1")

        if not host:
            log.warning("[VectorStore] OPENSEARCH_HOST is not set. Cannot connect to OpenSearch.")
            return None

        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region)

        client = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        return client
    except Exception as e:
        log.error("[VectorStore] Failed to initialize OpenSearch client: %s", e)
        return None

def _index_opensearch(chunks: list[dict], vectors: list) -> int:
    index_name = setting("OPENSEARCH_INDEX", "esa_documents")
    client = _get_opensearch_client()
    if not client:
        log.error("[VectorStore] OpenSearch client not initialized. Cannot index.")
        return 0

    # Ensure index exists with KNN mapping
    if not client.indices.exists(index=index_name):
        log.info("[VectorStore] Creating OpenSearch index: %s", index_name)
        client.indices.create(index=index_name, body={
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "embedding": {"type": "knn_vector", "dimension": len(vectors[0])},
                    "text":      {"type": "text"},
                    "source":    {"type": "keyword"},
                    "chunk":     {"type": "integer"}
                }
            }
        })

    # Bulk index
    actions = []
    for c, v in zip(chunks, vectors):
        actions.append({"index": {"_index": index_name, "_id": c["id"]}})
        actions.append({"embedding": v, "text": c["text"], "source": c["source"], "chunk": c["chunk"]})

    client.bulk(body=actions)
    log.info("[VectorStore] Output: %d chunks indexed in OpenSearch (%s)", len(chunks), index_name)
    return len(chunks)

def _search_opensearch(query_vector: list, k: int) -> list[dict]:
    index_name = setting("OPENSEARCH_INDEX", "esa_documents")
    client = _get_opensearch_client()
    if not client:
        log.error("[VectorStore] OpenSearch client not initialized. Cannot search.")
        return []

    body = {
        "size": k,
        "query": {
            "knn": {
                "embedding": {"vector": query_vector, "k": k}
            }
        }
    }
    response = client.search(index=index_name, body=body)
    hits = []
    for item in response.get("hits", {}).get("hits", []):
        source = item.get("_source", {})
        hits.append({
            "text": source.get("text", ""),
            "metadata": {
                "source": source.get("source", ""),
                "chunk":  source.get("chunk", "")
            }
        })
    return hits


# ── Public API ────────────────────────────────────────────────────────────────

def _get_backend() -> str:
    """Returns the active vector store backend: 'chromadb' or 'opensearch'."""
    backend = setting("VECTOR_STORE", "opensearch").lower()
    if backend not in ("chromadb", "opensearch"):
        log.warning("[VectorStore] Unknown VECTOR_STORE='%s', defaulting to 'opensearch'", backend)
        return "opensearch"
    return backend


def index_chunks(chunks: list[dict]) -> int:
    """
    Embed and index text chunks into the configured vector store backend.
    Backend selected by VECTOR_STORE in .env: 'chromadb' or 'opensearch'.
    """
    backend = _get_backend()
    log.info("[VectorStore] Backend: %s | Indexing %d chunks", backend.upper(), len(chunks))

    if not chunks:
        log.warning("[VectorStore] No chunks provided — nothing indexed.")
        return 0

    embed = _embedder()
    log.info("[VectorStore] Generating embeddings for %d chunks ...", len(chunks))
    vectors = embed([c["text"] for c in chunks])

    if backend == "chromadb":
        return _index_chromadb(chunks, vectors)
    else:
        return _index_opensearch(chunks, vectors)


def search_chunks(query: str, k: int = 4) -> list[dict]:
    """
    Perform a semantic similarity search against the configured vector store.
    Backend selected by VECTOR_STORE in .env: 'chromadb' or 'opensearch'.
    """
    backend = _get_backend()
    log.info("[VectorStore] Backend: %s | Search query: '%s' | top-k=%d", backend.upper(), query, k)

    embed = _embedder()
    query_vector = embed([query])[0]

    if backend == "chromadb":
        hits = _search_chromadb(query_vector, k)
    else:
        hits = _search_opensearch(query_vector, k)

    log.info("[VectorStore] Search returned %d result(s)", len(hits))
    return hits


def answer_query(query: str, k: int = 4) -> str:
    """
    Retrieve relevant passages and optionally generate an LLM answer.
    """
    log.info("[VectorStore] Answering query: '%s'", query)
    hits = search_chunks(query, k)
    context = "\n\n".join(f"Source: {h['metadata'].get('source')}\n{h['text']}" for h in hits)

    provider = setting("LLM_PROVIDER", "none").lower()

    if provider == "openai":
        log.info("[VectorStore] LLM: OpenAI chat (model=%s)", setting("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
        from openai import OpenAI
        client = OpenAI(api_key=setting("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=setting("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "Answer only from the provided context. If context is insufficient, say so."},
                {"role": "user",   "content": f"Question: {query}\n\nContext:\n{context}"}
            ])
        answer = response.choices[0].message.content or ""
        log.info("[VectorStore] Output: LLM-generated answer (%d chars)", len(answer))
        return answer

    log.info("[VectorStore] Output: Retrieved passages (LLM_PROVIDER=%s)", provider)
    return "Retrieved passages:\n\n" + context
