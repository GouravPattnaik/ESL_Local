"""
step6_document_processing.py
=============================
ESA Pipeline — Step 6: Document Processing + Vector Store Indexing

PURPOSE
-------
This script is Step 6 in the ESA pipeline. In the architecture it corresponds to:

  Document Sources (SharePoint / S3 / local)
      → Doc Processing (Amazon Bedrock / local text extraction)
      → Vector Store (Amazon OpenSearch)
      → Entity Resolution (link extracted entities to knowledge graph entities)
      → Generate RDF (Documents) → Neptune (Path A: SPARQL bulk, Path B: direct API)

Locally we:
  1. Walk the DOCS_DIR for supported files (.txt, .md, .pdf, .docx).
  2. Extract text from each document (pypdf for PDFs, python-docx for DOCX).
  3. Split text into overlapping chunks (chunk_chars=900, overlap=120 by default).
  4. Generate vector embeddings for each chunk:
       - Local mode: SentenceTransformers (all-MiniLM-L6-v2, free, offline)
       - OpenAI mode: OpenAI text-embedding-3-small (paid API, set OPENAI_API_KEY)
  5. Upsert chunks + embeddings into ChromaDB persistent collection.
  6. Run a quick verification search to confirm the index works.

WHY DOCUMENT PROCESSING SEPARATE FROM STRUCTURED DATA?
--------------------------------------------------------
Unstructured documents (policies, contracts, reports) contain business knowledge
not captured in structured tables. By embedding them into a vector store,
we enable semantic search ("RAG") — linking document passages to entity knowledge.

The architecture shows that after this step, Entity Resolution links extracted
document entities (e.g. "customer mentioned in policy") to canonical graph entities.
That linking is represented in Generate RDF (Documents) which creates document→entity
triples in Neptune (ex:document_123 ex:mentions ex:customer_C001).

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                              AWS
  ──────────────────────────────────────────────────────────────────────
  data/documents/                  → S3 bucket / SharePoint
  extract_text() in documents.py   → Amazon Bedrock (Claude) for extraction
  load_chunks() in documents.py    → Doc Processing Lambda
  index_chunks() in vectorstore.py → Amazon OpenSearch vector index
  ChromaDB local_bucket/vectorstore/ → OpenSearch Serverless collection

INPUTS
------
  - DOCS_DIR (.env) : Directory with .txt / .md / .pdf / .docx files
  - EMBEDDING_PROVIDER (.env) : "local" (free) or "openai" (paid API)
  - CHROMA_DIR (.env) : Persistent ChromaDB storage path

OUTPUTS
-------
  - ChromaDB collection at CHROMA_DIR with embedded document chunks
  - Verification search results printed to console
  - Console log with per-file chunk counts and embedding timings

RUN
---
  python scripts/step6_document_processing.py
  python scripts/step6_document_processing.py --verify "What is the loan policy?"
  python scripts/step6_document_processing.py --top-k 5
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
_log_file = setup_logging("step6_document_processing", _ROOT)
log = logging.getLogger("step6.document_processing")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Step 6 — Document Processing + Vector Store Indexing"
    )
    parser.add_argument(
        "--verify",
        default="What are the customer eligibility requirements?",
        help="Verification query to run after indexing (default: customer eligibility query).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of top results to retrieve in verification search (default: 3).",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=900,
        help="Characters per chunk (default: 900).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=120,
        help="Overlap characters between chunks (default: 120).",
    )
    args = parser.parse_args()

    from esa.config import setting
    from esa.documents import load_chunks
    from esa.vectorstore import index_chunks, search_chunks

    docs_dir       = setting("DOCS_DIR", "data/documents")
    chroma_dir     = setting("CHROMA_DIR", "local_bucket/vectorstore/chroma")
    embed_provider = setting("EMBEDDING_PROVIDER", "local")
    collection     = setting("CHROMA_COLLECTION", "esa_documents")

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 6: Document Processing + Vector Store Indexing")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Extract text from unstructured documents, create overlapping")
    log.info("          chunks, embed them, and index in ChromaDB vector store.")
    log.info("WHY     : Enables semantic search (RAG) over documents — linking")
    log.info("          policy text, contracts, and reports to the knowledge graph.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Documents directory  : %s", Path(docs_dir).resolve())
    log.info("  Embedding provider   : %s", embed_provider.upper())
    log.info("  Chunk size           : %d chars | Overlap: %d chars",
             args.chunk_chars, args.overlap)
    log.info("  ChromaDB collection  : %s at %s", collection, chroma_dir)
    log.info("  AWS equivalent (embed): Amazon Bedrock / OpenAI Embeddings API")
    log.info("  AWS equivalent (store): Amazon OpenSearch Serverless")
    log.info("────────────────────────────────────────────────────────────────────")

    if not Path(docs_dir).is_dir():
        log.error("FATAL: Documents directory not found: %s", Path(docs_dir).resolve())
        sys.exit(1)

    # ── Text extraction + chunking ────────────────────────────────────────────
    log.info("")
    log.info("▶ Extracting text and creating chunks ...")
    chunks = load_chunks(docs_dir, chunk_chars=args.chunk_chars, overlap=args.overlap)

    if not chunks:
        log.warning("  No chunks created — check DOCS_DIR contains supported files.")
        log.warning("  Supported: .txt .md .pdf .docx")
        sys.exit(0)

    # ── Embedding + indexing ──────────────────────────────────────────────────
    log.info("")
    log.info("▶ Generating embeddings and indexing into ChromaDB ...")
    log.info("  (First run may download SentenceTransformer model — ~90MB)")
    n_indexed = index_chunks(chunks)

    # ── Verification search ───────────────────────────────────────────────────
    log.info("")
    log.info("▶ Running verification search ...")
    log.info("  Query : '%s'", args.verify)
    log.info("  Top-K : %d", args.top_k)
    results = search_chunks(args.verify, k=args.top_k)

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  Chunks indexed      : %d", n_indexed)
    log.info("  ChromaDB path       : %s", Path(chroma_dir).resolve())
    log.info("  Verification hits   : %d result(s) returned", len(results))
    log.info("")
    for i, hit in enumerate(results, 1):
        src = hit["metadata"].get("source", "unknown")
        txt = hit["text"][:150].replace("\n", " ")
        log.info("  [Result %d] Source: %s", i, Path(src).name)
        log.info("             Text  : %s...", txt)
        log.info("")
    log.info("  AWS equivalent : s3://<bucket>/vectorstore/ → OpenSearch index")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 6 COMPLETE — Documents indexed in ChromaDB.")
    log.info("   Next step → Run: python scripts/step7_graph_load.py")
    log.info("   To ask questions: python scripts/step8_query_rag.py \"your question\"")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
