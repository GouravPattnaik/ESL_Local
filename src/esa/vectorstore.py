"""
vectorstore.py — Embedding generation and Vector index (OpenSearch).

Architecture alignment:
  - Corresponds to 'AWS OpenSearch / Vector Store' step in the architecture.
  - AWS OpenSearch used for index.

Input : list of text chunks (from documents.py)
Output: chunks indexed in OpenSearch; supports similarity search and optional LLM answering.
"""

import logging
import os
from .config import setting

log = logging.getLogger(__name__)

def _embedder():
    """
    Returns an embed function based on EMBEDDING_PROVIDER setting.
    """
    provider = setting("EMBEDDING_PROVIDER", "openai").lower()

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

    # Fallback to local
    model_name = setting("LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    log.info("[VectorStore] Embedder: Local SentenceTransformer (model=%s)", model_name)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    return lambda texts: model.encode(texts, normalize_embeddings=True).tolist()


def _get_opensearch_client():
    from opensearchpy import OpenSearch, RequestsHttpConnection
    try:
        from opensearchpy import AWSV4SignerAuth
        import boto3
        host = setting("OPENSEARCH_HOST", "")
        region = setting("AWS_REGION", "us-east-1")
        
        if not host:
            log.warning("[VectorStore] OPENSEARCH_HOST is not set. OpenSearch integration will fail.")
            return None
            
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region)
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        return client
    except Exception as e:
        log.error(f"[VectorStore] Failed to initialize OpenSearch client: {e}")
        return None

def index_chunks(chunks: list[dict]) -> int:
    """
    Embed and index text chunks into AWS OpenSearch.
    """
    index_name = setting("OPENSEARCH_INDEX", "esa_documents")
    log.info("[VectorStore] Input: %d chunks to index", len(chunks))
    
    if not chunks:
        log.warning("[VectorStore] No chunks provided — nothing indexed.")
        return 0

    embed = _embedder()
    log.info("[VectorStore] Generating embeddings for %d chunks ...", len(chunks))
    vectors = embed([c["text"] for c in chunks])
    
    client = _get_opensearch_client()
    if not client:
        log.error("[VectorStore] OpenSearch client not initialized. Cannot index.")
        return 0
        
    # Ensure index exists
    if not client.indices.exists(index=index_name):
        log.info(f"[VectorStore] Creating OpenSearch index: {index_name}")
        client.indices.create(index=index_name, body={
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "embedding": {"type": "knn_vector", "dimension": len(vectors[0])},
                    "text": {"type": "text"},
                    "source": {"type": "keyword"},
                    "chunk": {"type": "integer"}
                }
            }
        })
        
    # Bulk index
    actions = []
    for c, v in zip(chunks, vectors):
        actions.append({"index": {"_index": index_name, "_id": c["id"]}})
        actions.append({
            "embedding": v,
            "text": c["text"],
            "source": c["source"],
            "chunk": c["chunk"]
        })
        
    client.bulk(body=actions)
    log.info("[VectorStore] Output: %d chunks successfully embedded and indexed in OpenSearch", len(chunks))
    return len(chunks)


def search_chunks(query: str, k: int = 4) -> list[dict]:
    """
    Perform a semantic similarity search against the OpenSearch index.
    """
    index_name = setting("OPENSEARCH_INDEX", "esa_documents")
    log.info("[VectorStore] Search query: '%s' | top-k=%d", query, k)
    
    client = _get_opensearch_client()
    if not client:
        log.error("[VectorStore] OpenSearch client not initialized. Cannot search.")
        return []

    embed = _embedder()
    query_vector = embed([query])[0]
    
    body = {
        "size": k,
        "query": {
            "knn": {
                "embedding": {
                    "vector": query_vector,
                    "k": k
                }
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
                "chunk": source.get("chunk", "")
            }
        })
        
    log.info("[VectorStore] Search returned %d result(s)", len(hits))
    return hits


def answer_query(query: str, k: int = 4) -> str:
    """
    Retrieve relevant passages and optionally generate an LLM answer.
    """
    log.info("[VectorStore] Answering query: '%s'", query)
    hits = search_chunks(query, k)
    context = "\n\n".join(f"Source: {h['metadata'].get('source')}\n{h['text']}" for h in hits)

    # Use OpenAI by default for the LLM answering part
    provider = setting("LLM_PROVIDER", "openai").lower()
    
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
