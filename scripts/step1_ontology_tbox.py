"""
step1_ontology_tbox.py
======================
ESA Pipeline — Step 1: Ontology / T-Box Ingestion

PURPOSE
-------
This script is the first step in the Enterprise Semantic Analytics (ESA) pipeline.
In the architecture it corresponds to:

  CENtree Ontology Authoring
      → (Publish Webhook / Manual Export)
      → AWS Lambda: ExportOntology
      → PyOxigraph: Parse + Serialize to N-Quads
      → S3 (Validated T-Box)
      → Amazon Neptune (T-Box / Named Graph)

Locally we:
  1. Read the CENtree Turtle ontology file (.ttl) — the T-Box vocabulary
     (classes, properties, relationships).
  2. Parse it with PyOxigraph into an in-memory RDF store.
  3. Serialize it to N-Quads format (.nq) — the canonical wire format for Neptune.
  4. Optionally run SHACL validation against a shapes file.
  5. Write the N-Quads to local_bucket/ontology/centree/ontology.nq
     (mirrors the S3 key layout for easy cloud migration).

WHY T-BOX FIRST?
----------------
The T-Box defines the vocabulary (schema). All A-Box instance data
(customers, accounts, loans) must conform to this vocabulary.
Running T-Box ingestion first ensures downstream steps have a validated
schema to reference.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                          AWS
  ─────────────────────────────────────────────────────
  data/centree/ontology.ttl   → CENtree export (via API / webhook)
  PyOxigraph parse             → Lambda: ExportOntology (PyOxigraph)
  SHACL validate               → pySHACL validation
  local_bucket/ontology/       → S3 bucket (Validated T-Box)
  (manual review)              → Alert and Feedback (SNS/email)

INPUTS
------
  - CENTREE_TTL_PATH  (.env)  : Path to Turtle ontology file
  - SHACL_SHAPES_PATH (.env)  : Path to SHACL shapes file (optional)

OUTPUTS
-------
  - local_bucket/ontology/centree/ontology.nq  : N-Quads T-Box
  - Console log showing triple count and any SHACL violations

RUN
---
  python scripts/step1_ontology_tbox.py
  python scripts/step1_ontology_tbox.py --validate-shacl
"""

import sys
import logging
import argparse
from pathlib import Path
from datetime import datetime

# ── Path bootstrap (allow running from repo root or scripts/) ─────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# ── Logging setup (UTF-8 safe for Windows) ───────────────────────────────────
from _logging_setup import setup_logging
_log_file = setup_logging("step1_ontology", _ROOT)
log = logging.getLogger("step1.ontology_tbox")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Step 1 — Ontology / T-Box ingestion"
    )
    parser.add_argument(
        "--validate-shacl",
        action="store_true",
        help="Run pySHACL validation after parsing (requires pyshacl installed).",
    )
    parser.add_argument(
        "--ttl-path",
        default=None,
        help="Override CENTREE_TTL_PATH from .env (e.g. data/centree/ontology.ttl).",
    )
    args = parser.parse_args()

    from esa.config import setting
    from esa.rdf import parse_tbox, validate_shacl
    from esa.storage import write_local

    ttl_path = args.ttl_path or setting("CENTREE_TTL_PATH", "data/centree/ontology.ttl")
    shapes_path = setting("SHACL_SHAPES_PATH", "data/shapes/ontology_shapes.ttl")

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 1: Ontology / T-Box Ingestion")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Parse the CENtree ontology Turtle file into RDF N-Quads.")
    log.info("WHY     : T-Box (vocabulary/schema) must be loaded before A-Box")
    log.info("          (instance data) so Neptune can validate class/property usage.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Ontology Turtle file : %s", Path(ttl_path).resolve())
    log.info("  File exists          : %s", Path(ttl_path).is_file())
    log.info("────────────────────────────────────────────────────────────────────")

    # ── Parse T-Box ───────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Parsing Turtle ontology with PyOxigraph ...")
    try:
        nquads_bytes, triple_count = parse_tbox(ttl_path)
    except FileNotFoundError as exc:
        log.error("FATAL: %s", exc)
        log.error(
            "Fix : Ensure CENTREE_TTL_PATH in .env points to a valid .ttl file."
        )
        sys.exit(1)

    log.info("  ✓ Parsed %d RDF triples from ontology", triple_count)

    # ── Optional SHACL validation ─────────────────────────────────────────────
    if args.validate_shacl:
        log.info("")
        log.info("▶ Running SHACL validation (shapes: %s) ...", shapes_path)
        try:
            result = validate_shacl(nquads_bytes, shapes_path)
            if result["conforms"]:
                log.info("  ✓ SHACL PASSED — T-Box conforms to shapes graph")
            else:
                log.warning("  ✗ SHACL FAILED — Ontology does NOT conform to shapes!")
                log.warning("  Report:\n%s", result["report"])
        except ImportError:
            log.warning(
                "  SHACL skipped — pyshacl not installed. Run: pip install pyshacl"
            )
    else:
        log.info("  ℹ SHACL validation skipped (pass --validate-shacl to enable)")

    # ── Persist N-Quads ───────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Writing N-Quads to local bucket ...")
    out_key = "ontology/centree/ontology.nq"
    out_path = write_local(out_key, nquads_bytes)

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  N-Quads file   : %s", Path(out_path).resolve())
    log.info("  Triple count   : %d", triple_count)
    log.info("  File size      : %d bytes", len(nquads_bytes))
    log.info("  AWS equivalent : s3://<bucket>/ontology/centree/ontology.nq")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 1 COMPLETE — T-Box N-Quads written successfully.")
    log.info("   Next step → Run: python scripts/step2_metadata_normalisation.py")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
