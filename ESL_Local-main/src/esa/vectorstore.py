"""
vectorstore.py — Embedding generation and ChromaDB vector index.

Architecture alignment:
  - Corresponds to 'AWS OpenSearch / Vector Store' step in the architecture.
  - Local: SentenceTransformers + ChromaDB (persistent, on-disk).
  - Cloud option: OpenAI embeddings + Amazon OpenSearch (see comments).

Input : list of text chunks (from documents.py)
Output: chunks indexed in ChromaDB; supports similarity search and optional LLM answering.
"""

import logging
from pathlib import Path
import chromadb
from .config import setting

log = logging.getLogger(__name__)


def _embedder():
    """
    Returns an embed function based on EMBEDDING_PROVIDER setting.
    - 'local'  → SentenceTransformers (downloads model on first run)
    - 'openai' → OpenAI Embeddings API (requires OPENAI_API_KEY)
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

    model_name = setting("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    log.info("[VectorStore] Embedder: Local SentenceTransformer (model=%s)", model_name)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    return lambda texts: model.encode(texts, normalize_embeddings=True).tolist()


def index_chunks(chunks: list[dict]) -> int:
    """
    Embed and upsert text chunks into the ChromaDB vector store.

    Input : list of chunk dicts {id, text, source, chunk}
    Output: number of chunks indexed (int)
    """
    path       = setting("CHROMA_DIR", "local_bucket/vectorstore/chroma")
    collection = setting("CHROMA_COLLECTION", "esa_documents")

    log.info("[VectorStore] Input: %d chunks to index", len(chunks))
    log.info("[VectorStore] Target: ChromaDB collection='%s' at path='%s'", collection, path)

    if not chunks:
        log.warning("[VectorStore] No chunks provided — nothing indexed.")
        return 0

    Path(path).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=path)
    col    = client.get_or_create_collection(collection)

    embed   = _embedder()
    log.info("[VectorStore] Generating embeddings for %d chunks ...", len(chunks))
    vectors = embed([c["text"] for c in chunks])

    col.upsert(
        ids        = [c["id"]     for c in chunks],
        documents  = [c["text"]   for c in chunks],
        metadatas  = [{"source": c["source"], "chunk": c["chunk"]} for c in chunks],
        embeddings = vectors
    )

    log.info("[VectorStore] Output: %d chunks successfully embedded and indexed in ChromaDB", len(chunks))
    return len(chunks)


def search_chunks(query: str, k: int = 4) -> list[dict]:
    """
    Perform a semantic similarity search against the ChromaDB collection.

    Input : query string, top-k results to return
    Output: list of {text, metadata} dicts
    """
    path       = setting("CHROMA_DIR", "local_bucket/vectorstore/chroma")
    collection = setting("CHROMA_COLLECTION", "esa_documents")

    log.info("[VectorStore] Search query: '%s' | top-k=%d", query, k)
    client = chromadb.PersistentClient(path=path)
    col    = client.get_or_create_collection(collection)

    embed  = _embedder()
    result = col.query(query_embeddings=embed([query]), n_results=k)
    docs   = result.get("documents",  [[]])[0]
    metas  = result.get("metadatas",  [[]])[0]

    hits = [{"text": d, "metadata": m} for d, m in zip(docs, metas)]
    log.info("[VectorStore] Search returned %d result(s)", len(hits))
    for i, h in enumerate(hits):
        log.debug("[VectorStore]   Result %d: source=%s", i + 1, h["metadata"].get("source"))
    return hits


def answer_query(query: str, k: int = 4) -> str:
    """
    Retrieve relevant passages and optionally generate an LLM answer.

    Input : query string
    Output: answer string (retrieved passages if LLM_PROVIDER=none, or LLM-generated answer)
    """
    log.info("[VectorStore] Answering query: '%s'", query)
    hits    = search_chunks(query, k)
    context = "\n\n".join(f"Source: {h['metadata'].get('source')}\n{h['text']}" for h in hits)

    if setting("LLM_PROVIDER", "none").lower() == "openai":
        log.info("[VectorStore] LLM: OpenAI chat (model=%s)", setting("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
        from openai import OpenAI
        client   = OpenAI(api_key=setting("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=setting("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "Answer only from the provided context. If context is insufficient, say so."},
                {"role": "user",   "content": f"Question: {query}\n\nContext:\n{context}"}
            ])
        answer = response.choices[0].message.content or ""
        log.info("[VectorStore] Output: LLM-generated answer (%d chars)", len(answer))
        return answer

    log.info("[VectorStore] Output: Retrieved passages (LLM_PROVIDER=none)")
    return "Retrieved passages (no LLM configured):\n\n" + context
