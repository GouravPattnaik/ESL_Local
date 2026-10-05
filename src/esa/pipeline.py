"""
pipeline.py — Top-level orchestration of the ESA local POC pipeline.

Architecture alignment (local POC → AWS mapping):
  Step 1: Ontology (T-Box) ingestion  → Lambda: ontology_parse_validate → S3 (validated T-Box)
  Step 2: Entity Resolution           → Lambda: entity_resolution        → S3 (ER report)
  Step 3: A-Box RDF generation        → Lambda: abox_rdf_build           → S3 (A-Box N-Quads)
  Step 4: Document indexing           → Lambda: embed_index              → ChromaDB / OpenSearch

Local storage (local_bucket/) mirrors S3 bucket layout for easy cloud migration.
"""

import json
import logging
from pathlib import Path

from .config import setting
from .storage import write_artifact
from .rdf import parse_tbox, build_abox
from .documents import load_chunks
from .vectorstore import index_chunks
from .entity_resolution import resolve_customers

log = logging.getLogger(__name__)


# ── Step 1: Ontology / T-Box ──────────────────────────────────────────────────

def run_ontology() -> dict:
    """
    Parse the CENtree ontology Turtle (T-Box) and persist it as N-Quads.

    Input : CENTREE_TTL_PATH (.env) → ontology Turtle file
    Output: N-Quads file written to local_bucket/ontology/centree/ontology.nq
    """
    ttl_path = setting("CENTREE_TTL_PATH", "data/centree/ontology.ttl")
    log.info("=" * 60)
    log.info("STEP 1 — Ontology / T-Box Ingestion")
    log.info("  Input : %s", ttl_path)

    data, count = parse_tbox(ttl_path)

    key  = "ontology/centree/ontology.nq"
    path = write_artifact(key, data)
    log.info("  Output: %d triples → %s", count, path)
    return {"step": "ontology", "triple_count": count, "path": path}


# ── Step 2 + 3: Entity Resolution + A-Box generation ─────────────────────────

def run_abox() -> dict:
    """
    Load structured records (customers, accounts, loans), run Entity Resolution,
    then generate A-Box RDF and persist results.

    Input : STRUCTURED_DATA_PATH directory containing customers.json, accounts.json, loans.json
    Output:
      - local_bucket/knowledge/abox/instances.nq     (A-Box RDF N-Quads)
      - local_bucket/knowledge/entity_resolution/results.json
    """
    records_dir = Path(setting("STRUCTURED_DATA_PATH", "data/records"))
    log.info("=" * 60)
    log.info("STEP 2 — Structured Data Load + Entity Resolution + A-Box RDF")
    log.info("  Input directory: %s", records_dir.resolve())

    # Load each table
    customers = _load_json(records_dir / "customers.json", "customers")
    accounts  = _load_json(records_dir / "accounts.json",  "accounts")
    loans     = _load_json(records_dir / "loans.json",     "loans")

    # Entity resolution on customers
    log.info("  Running Entity Resolution on customers ...")
    er = resolve_customers(customers)

    # Build A-Box RDF
    log.info("  Building A-Box RDF from all tables ...")
    rdf, triple_count = build_abox(customers, accounts, loans)

    # Persist
    key      = setting("RDF_OUTPUT_KEY", "knowledge/abox/instances.nq")
    rdf_path = write_artifact(key, rdf)
    er_path  = write_artifact(
        "knowledge/entity_resolution/results.json",
        json.dumps(er, indent=2).encode()
    )

    log.info("  Output (RDF)      : %d triples → %s", triple_count, rdf_path)
    log.info("  Output (ER report): %s", er_path)
    log.info("  ER summary: %d canonical entities | %d review candidates",
             len(er["canonical_entities"]), len(er["review_candidates"]))

    return {
        "step":         "abox",
        "record_counts": {"customers": len(customers), "accounts": len(accounts), "loans": len(loans)},
        "triple_count": triple_count,
        "rdf_path":     rdf_path,
        "er_path":      er_path,
        "er_candidates": len(er["review_candidates"])
    }


# ── Step 4: Document Processing + Vector Indexing ────────────────────────────

def run_documents() -> dict:
    """
    Extract text from documents, chunk, embed, and index into ChromaDB.

    Input : DOCS_DIR (.env) → directory of unstructured documents
    Output: Chunks embedded and stored in ChromaDB at CHROMA_DIR
    """
    docs_dir  = setting("DOCS_DIR", "data/documents")
    chroma_dir = setting("CHROMA_DIR", "local_bucket/vectorstore/chroma")
    log.info("=" * 60)
    log.info("STEP 3 — Document Extraction + Embedding + Vector Indexing")
    log.info("  Input directory: %s", docs_dir)

    chunks = load_chunks(docs_dir)
    count  = index_chunks(chunks)

    log.info("  Output: %d chunks indexed in ChromaDB at '%s'", count, chroma_dir)
    return {"step": "documents", "chunks_indexed": count, "vector_store": chroma_dir}


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_all() -> list[dict]:
    """Run the full local pipeline in sequence and return step results."""
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║   ESA Local POC Pipeline — Starting                     ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    results = [run_ontology(), run_abox(), run_documents()]
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║   ESA Local POC Pipeline — Complete                     ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path, label: str) -> list[dict]:
    if not path.is_file():
        log.warning("  [%s] File not found: %s — returning empty list", label, path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    log.info("  [%s] Loaded %d records from %s", label, len(data), path.name)
    return data
