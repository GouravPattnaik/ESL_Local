"""
step5_abox_rdf_generation.py
=============================
ESA Pipeline — Step 5: A-Box RDF Generation + SHACL Validation

PURPOSE
-------
This script is Step 5 in the ESA pipeline. In the architecture it corresponds to:

  Resolved Entities (from Step 4)
  + Approved Mappings (from Step 3)
      → RDF Generation (PyOxigraph)  — builds A-Box N-Quads
      → Data Validation (pySHACL)    — validates A-Box against SHACL shapes
      → Amazon S3 (A-Box Data)       — stores validated N-Quads
      → Amazon Neptune               — bulk load for SPARQL queries

Locally we:
  1. Load normalised records (Step 2 output, fallback to raw).
  2. Read approved semantic mappings (Step 3 output) for audit logging.
  3. Build A-Box RDF using PyOxigraph — creating typed named nodes and quads
     for Customer, Account, Loan, and Branch with all their properties.
  4. Run pySHACL validation against the shapes file (validates data quality).
  5. Write A-Box N-Quads to local_bucket/knowledge/abox/instances.nq
     (mirrors the S3 → Neptune bulk load path).

WHY A-BOX RDF AFTER ENTITY RESOLUTION?
----------------------------------------
Entity resolution (Step 4) ensures we create ONE canonical IRI per entity.
After ER, we know:
  - cust_C001 and cust_C001 are the same → one ex:customer/C001 node
  - "Ravi Kumar" and "R. Kumar" → flagged for human review before merging

SHACL validation then checks whether the generated RDF conforms to the
shapes defined by the business team (e.g. every Customer MUST have a customerId
and fullName). This is the "data validation" gate before Neptune loading.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                              AWS
  ──────────────────────────────────────────────────────────────────────
  local_bucket/normalised/*.json   → S3 (Normalised Metadata / ER output)
  build_abox() in rdf.py           → Lambda: RDF Generation (PyOxigraph)
  validate_shacl() in rdf.py       → Lambda: Data Validation (pySHACL)
  local_bucket/knowledge/abox/     → S3 A-Box bucket + Neptune Bulk Load

EXAMPLE A-BOX TRIPLES GENERATED
---------------------------------
  ex:customer/C001  rdf:type          ex:Customer .
  ex:customer/C001  ex:customerId     "C001"^^xsd:string .
  ex:customer/C001  ex:fullName       "Alice Johnson"^^xsd:string .
  ex:customer/C001  ex:creditScore    780^^xsd:integer .
  ex:customer/C001  ex:registeredAt   ex:branch/BR001 .
  ex:customer/C001  ex:hasLoan        ex:loan/L001 .
  ex:loan/L001      rdf:type          ex:Loan .
  ex:loan/L001      ex:loanAmount     500000.0^^xsd:decimal .

INPUTS
------
  - local_bucket/normalised/customers.json  (Step 2, or fallback raw)
  - local_bucket/normalised/accounts.json   (Step 2, or fallback raw)
  - local_bucket/normalised/loans.json      (Step 2, or fallback raw)
  - local_bucket/semantic_mapping/approved_mappings.json  (Step 3, for audit)
  - data/shapes/ontology_shapes.ttl         (for SHACL validation)

OUTPUTS
-------
  - local_bucket/knowledge/abox/instances.nq   : A-Box RDF N-Quads
  - Console log with triple count, SHACL result, sample triples

RUN
---
  python scripts/step5_abox_rdf_generation.py
  python scripts/step5_abox_rdf_generation.py --skip-shacl
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

# ── Path bootstrap ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# ── Logging setup ─────────────────────────────────────────────────────────────
from _logging_setup import setup_logging
_log_file = setup_logging("step5_abox_rdf", _ROOT)
log = logging.getLogger("step5.abox_rdf_generation")


def _load_json(path: Path, label: str, fallback: Path | None = None) -> list[dict]:
    """Load JSON list from path, falling back to fallback if not found."""
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        log.info("  [%s] Loaded %d records from: %s", label, len(data), path)
        return data
    if fallback and fallback.is_file():
        log.warning(
            "  [%s] Normalised file not found (%s) — using raw fallback: %s",
            label, path, fallback,
        )
        data = json.loads(fallback.read_text(encoding="utf-8"))
        log.info("  [%s] Loaded %d records (raw fallback)", label, len(data))
        return data
    log.warning("  [%s] No data found at %s or %s — returning empty list", label, path, fallback)
    return []


def print_sample_triples(nquads_bytes: bytes, n: int = 8) -> None:
    """Print first n triples from N-Quads for inspection."""
    lines = nquads_bytes.decode("utf-8").strip().split("\n")
    log.info("  Sample triples (first %d of %d):", min(n, len(lines)), len(lines))
    for line in lines[:n]:
        log.info("    %s", line[:120])  # truncate long lines


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Step 5 — A-Box RDF Generation + SHACL Validation"
    )
    parser.add_argument(
        "--skip-shacl",
        action="store_true",
        help="Skip SHACL validation (useful if pyshacl is not installed).",
    )
    args = parser.parse_args()

    from esa.config import setting
    from esa.rdf import build_abox, validate_shacl
    from esa.storage import write_local

    local_bucket  = Path(setting("LOCAL_BUCKET_DIR", "local_bucket"))
    records_dir   = Path(setting("STRUCTURED_DATA_PATH", "data/records"))
    shapes_path   = setting("SHACL_SHAPES_PATH", "data/shapes/ontology_shapes.ttl")
    rdf_output_key = setting("RDF_OUTPUT_KEY", "knowledge/abox/instances.nq")

    normalised_dir = local_bucket / "normalised"
    mappings_file  = local_bucket / "semantic_mapping" / "approved_mappings.json"

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 5: A-Box RDF Generation + SHACL Validation")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Build RDF instance data (A-Box) from normalised records,")
    log.info("          then validate it against SHACL shapes for data quality.")
    log.info("WHY     : A-Box N-Quads are the canonical form loaded into Neptune.")
    log.info("          SHACL ensures every instance conforms to business rules")
    log.info("          before publishing to the knowledge graph.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Normalised dir  : %s", normalised_dir.resolve())
    log.info("  Approved maps   : %s", mappings_file.resolve())
    log.info("  SHACL shapes    : %s", Path(shapes_path).resolve())
    log.info("  SHACL validate  : %s", "NO (--skip-shacl)" if args.skip_shacl else "YES")
    log.info("────────────────────────────────────────────────────────────────────")

    # ── Load approved mappings for audit ──────────────────────────────────────
    if mappings_file.is_file():
        mappings = json.loads(mappings_file.read_text(encoding="utf-8"))
        log.info("")
        log.info("▶ Semantic Mapping audit (from Step 3):")
        for m in mappings.get("approved_mappings", []):
            log.info("  %-20s → %-55s (%s)", m["field"], m["iri"], m["datatype"])
    else:
        log.warning(
            "  Approved mappings not found — semantic mapping audit skipped."
        )
        log.warning(
            "  Tip: Run Step 3 → python scripts/step3_semantic_mapping.py"
        )

    # ── Load records ──────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Loading normalised records ...")
    customers = _load_json(normalised_dir / "customers.json", "customers", records_dir / "customers.json")
    accounts  = _load_json(normalised_dir / "accounts.json",  "accounts",  records_dir / "accounts.json")
    loans     = _load_json(normalised_dir / "loans.json",     "loans",     records_dir / "loans.json")

    # ── Build A-Box RDF ───────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Building A-Box RDF with PyOxigraph ...")
    log.info("  Creating Customer nodes (with Branch relationships) ...")
    log.info("  Creating Account nodes (with Customer→ownsAccount links) ...")
    log.info("  Creating Loan nodes (with Customer→hasLoan + Loan→securedBy links) ...")

    nquads_bytes, triple_count = build_abox(customers, accounts, loans)

    log.info("")
    log.info("▶ Sample A-Box triples generated:")
    print_sample_triples(nquads_bytes)

    # ── SHACL Validation ──────────────────────────────────────────────────────
    shacl_result = {"conforms": None, "skipped": True}
    if not args.skip_shacl:
        log.info("")
        log.info("▶ Running SHACL validation ...")
        log.info("  Shapes file: %s", shapes_path)
        try:
            shacl_result = validate_shacl(nquads_bytes, shapes_path)
            shacl_result["skipped"] = False
            if shacl_result["conforms"]:
                log.info("  ✓ SHACL PASSED — A-Box conforms to all shapes")
            else:
                log.warning("  ✗ SHACL FAILED — A-Box does NOT fully conform to shapes!")
                log.warning("  Report:\n%s", shacl_result["report"])
                log.warning("  Action: Review violations above and fix source data or shapes.")
        except ImportError:
            log.warning(
                "  pySHACL not installed — skipping. Run: pip install pyshacl"
            )
    else:
        log.info("  ℹ SHACL validation skipped (--skip-shacl flag)")

    # ── Persist ───────────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Writing A-Box N-Quads to local bucket ...")
    out_path = write_local(rdf_output_key, nquads_bytes)

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  A-Box N-Quads file : %s", Path(out_path).resolve())
    log.info("  Total RDF triples  : %d", triple_count)
    log.info("  File size          : %d bytes", len(nquads_bytes))
    log.info("  SHACL result       : %s",
             "SKIPPED" if shacl_result["skipped"] else
             ("PASSED" if shacl_result["conforms"] else "FAILED"))
    log.info("  AWS equivalents    :")
    log.info("    s3://<bucket>/knowledge/abox/instances.nq   (S3 A-Box)")
    log.info("    Neptune bulk load → SPARQL endpoint ready")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 5 COMPLETE — A-Box RDF generated and written.")
    log.info("   Next step → Run: python scripts/step6_document_processing.py")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
