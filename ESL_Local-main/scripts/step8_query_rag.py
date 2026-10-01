"""
step8_query_rag.py
===================
ESA Pipeline — Step 8: Query / RAG (Retrieval-Augmented Generation)

PURPOSE
-------
This script is Step 8 — the "query" layer on top of the complete ESA pipeline.
It is NOT a core pipeline step but the interactive interface for using the built
knowledge graph + vector store. In the architecture it corresponds to:

  Unified Enterprise Knowledge Graph (Neptune)
  + Vector Store (OpenSearch)
      → Query with SPARQL (structured RDF queries)
      → Semantic search (RAG over documents)
      → Optional LLM answer generation (OpenAI / Bedrock)

Locally we:
  1. Accept a natural-language question from the command line.
  2. Search the ChromaDB vector store for relevant document passages.
  3. Return the top-K passage results.
  4. If LLM_PROVIDER=openai, send context + question to OpenAI chat to generate
     a grounded answer.
  5. Show SPARQL query examples that would be equivalent in Neptune.

WHY RAG (RETRIEVAL-AUGMENTED GENERATION)?
-------------------------------------------
Raw document text is indexed (Step 6). When a user asks a question:
  1. The question is embedded into the same vector space as the document chunks.
  2. The most semantically similar chunks are retrieved.
  3. An LLM uses these retrieved chunks as context to answer — grounded in your docs.

This avoids LLM hallucination because the answer is constrained to retrieved context.
The architecture combines this with the graph (Neptune) to also pull structured facts.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                              AWS
  ──────────────────────────────────────────────────────────────────────
  search_chunks() / answer_query()  → Amazon Bedrock / OpenAI API
  ChromaDB local vector search      → Amazon OpenSearch semantic search
  Neo4j Cypher (graph queries)      → Amazon Neptune SPARQL
  (this script)                     → Query API Lambda (optional, Step 26)

INPUTS
------
  - Question from command line (positional argument)
  - CHROMA_DIR (.env) : Must be populated by Step 6
  - LLM_PROVIDER (.env) : "none" (retrieval only) or "openai" (LLM answer)
  - OPENAI_API_KEY (.env) : Required if LLM_PROVIDER=openai

OUTPUTS
-------
  - Retrieved document passages printed to console
  - (Optional) LLM-generated answer grounded in retrieved context
  - Equivalent SPARQL queries for Neptune graph

RUN
---
  python scripts/step8_query_rag.py "What is the loan eligibility criteria?"
  python scripts/step8_query_rag.py "What happens if a customer defaults?"
  python scripts/step8_query_rag.py --top-k 5 "Summarize the branch policies"
"""

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime

# ── Path bootstrap ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# ── Logging setup ─────────────────────────────────────────────────────────────
from _logging_setup import setup_logging
_log_file = setup_logging("step8_query_rag", _ROOT)
log = logging.getLogger("step8.query_rag")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Step 8 — Query / RAG (Retrieval-Augmented Generation)"
    )
    parser.add_argument(
        "question",
        nargs="*",
        default=["What are the customer loan eligibility requirements?"],
        help="Question to answer using the document vector store.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=4,
        help="Number of top document passages to retrieve (default: 4).",
    )
    args = parser.parse_args()
    query = " ".join(args.question)

    from esa.config import setting
    from esa.vectorstore import search_chunks, answer_query

    chroma_dir    = setting("CHROMA_DIR", "local_bucket/vectorstore/chroma")
    collection    = setting("CHROMA_COLLECTION", "esa_documents")
    llm_provider  = setting("LLM_PROVIDER", "none").lower()
    embed_provider = setting("EMBEDDING_PROVIDER", "local").lower()

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 8: Query / RAG")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Answer questions over indexed documents using semantic search.")
    log.info("WHY     : RAG (Retrieval-Augmented Generation) retrieves relevant passages")
    log.info("          then optionally uses an LLM to generate a grounded answer.")
    log.info("          This avoids hallucination by constraining LLM to retrieved context.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Question         : %s", query)
    log.info("  Top-K results    : %d", args.top_k)
    log.info("  Embedding model  : %s", embed_provider.upper())
    log.info("  LLM provider     : %s", llm_provider.upper())
    log.info("  ChromaDB path    : %s", Path(chroma_dir).resolve())
    log.info("  Collection       : %s", collection)
    log.info("────────────────────────────────────────────────────────────────────")

    if not Path(chroma_dir).is_dir():
        log.error("FATAL: ChromaDB not found at: %s", Path(chroma_dir).resolve())
        log.error("Fix: Run Step 6 first → python scripts/step6_document_processing.py")
        sys.exit(1)

    # ── Semantic search ───────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Performing semantic search ...")
    hits = search_chunks(query, k=args.top_k)

    if not hits:
        log.warning("  No results found. Check that Step 6 was run and documents are indexed.")
        sys.exit(0)

    log.info("")
    log.info("── RETRIEVED PASSAGES ───────────────────────────────────────────────")
    for i, hit in enumerate(hits, 1):
        src = hit["metadata"].get("source", "unknown")
        chunk_idx = hit["metadata"].get("chunk", "?")
        text = hit["text"]
        log.info("")
        log.info("  [Passage %d] Source: %s | Chunk: %s", i, Path(src).name, chunk_idx)
        log.info("  " + "─" * 60)
        # Print in readable blocks
        for line in text.split("\n"):
            if line.strip():
                log.info("  %s", line.strip())
    log.info("─" * 66)

    # ── LLM Answer (if configured) ────────────────────────────────────────────
    log.info("")
    if llm_provider == "openai":
        log.info("▶ Generating LLM answer (OpenAI) ...")
        try:
            answer = answer_query(query, k=args.top_k)
            log.info("")
            log.info("── GENERATED ANSWER ─────────────────────────────────────────────────")
            log.info("")
            for line in answer.split("\n"):
                log.info("  %s", line)
            log.info("─" * 66)
        except Exception as exc:
            log.error("  LLM answer failed: %s", exc)
            log.error("  Check OPENAI_API_KEY is set and valid in .env")
    else:
        log.info("  ℹ LLM answer disabled (LLM_PROVIDER=%s)", llm_provider)
        log.info("  To enable: set LLM_PROVIDER=openai and OPENAI_API_KEY in .env")

    # ── SPARQL equivalents ────────────────────────────────────────────────────
    log.info("")
    log.info("▶ AWS Neptune SPARQL equivalents (for graph-based queries):")
    log.info("")
    log.info("  # Find all customers with credit score below 700:")
    log.info("  PREFIX ex: <https://example.org/esa/>")
    log.info("  SELECT ?name ?score WHERE {")
    log.info("    ?c a ex:Customer ;")
    log.info("       ex:fullName ?name ;")
    log.info("       ex:creditScore ?score .")
    log.info("    FILTER(?score < 700)")
    log.info("  }")
    log.info("")
    log.info("  # Documents mentioning a specific customer:")
    log.info("  PREFIX ex: <https://example.org/esa/>")
    log.info("  SELECT ?doc WHERE {")
    log.info("    ?doc ex:mentions ex:customer/C001 .")
    log.info("  }")

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  Passages retrieved : %d", len(hits))
    log.info("  LLM answer         : %s", "YES" if llm_provider == "openai" else "NO (set LLM_PROVIDER=openai)")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 8 COMPLETE — Query answered.")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
