"""
step4_entity_resolution.py
===========================
ESA Pipeline — Step 4: Entity Resolution

PURPOSE
-------
This script is Step 4 in the ESA pipeline. In the architecture it corresponds to:

  Processed Content (from Doc Processing)
  + Normalised Metadata (from Step 2)
      → Entity Resolution (Lambda / ML — Ravi, R. Kumar, Ravi Kumar)
      → Resolved Entities / Unified View
      → Semantic Mapping (feeds back to Step 3 for document-entity links)

Locally we:
  1. Load normalised customer records (from Step 2 / local_bucket/normalised/).
  2. Run ID-based entity resolution (strong key: customer_id).
  3. Run fuzzy name similarity scan (RapidFuzz) to surface manual-review candidates.
  4. Produce a structured ER report with:
       - canonical_entities  : one canonical record per unique customer_id
       - decisions           : per-record identity decision log
       - review_candidates   : fuzzy name pairs flagged for human review
  5. Write ER results to local_bucket/knowledge/entity_resolution/results.json

WHY ENTITY RESOLUTION BEFORE A-BOX RDF?
-----------------------------------------
If multiple source records refer to the same real-world customer (e.g., due to data
entry errors, system migrations, or name variations), generating A-Box RDF without ER
creates duplicate nodes in the knowledge graph. ER ensures one canonical IRI per entity.

ARCHITECTURE NOTE ON FUZZY MATCHING
-------------------------------------
The "Ravi, R. Kumar, Ravi Kumar" example in the architecture diagram shows that ER
must handle name variations. This script flags these as manual-review candidates
(threshold=96% similarity). It NEVER auto-merges records based on names alone —
only verified IDs drive automatic canonical identity.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                                      AWS
  ──────────────────────────────────────────────────────────────────────
  local_bucket/normalised/customers.json   → S3 (Normalised Metadata)
  resolve_customers() in entity_resolution.py → Lambda: Entity Resolution (ML)
  local_bucket/knowledge/entity_resolution/ → S3 (ER output)
  review_candidates flagged in logs         → Manual review / Jira ticket

INPUTS
------
  - local_bucket/normalised/customers.json  (from Step 2)
    OR data/records/customers.json           (fallback if Step 2 not run)

OUTPUTS
-------
  - local_bucket/knowledge/entity_resolution/results.json
  - Console log showing canonical entities and review candidates

RUN
---
  python scripts/step4_entity_resolution.py
  python scripts/step4_entity_resolution.py --threshold 90
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
_log_file = setup_logging("step4_entity_resolution", _ROOT)
log = logging.getLogger("step4.entity_resolution")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Step 4 — Entity Resolution"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=96,
        help="Fuzzy name similarity threshold %% for review candidates (default: 96).",
    )
    args = parser.parse_args()

    from esa.config import setting
    from esa.entity_resolution import resolve_customers
    from esa.storage import write_local

    local_bucket = Path(setting("LOCAL_BUCKET_DIR", "local_bucket"))
    records_dir  = Path(setting("STRUCTURED_DATA_PATH", "data/records"))

    # Prefer normalised records (Step 2 output), fall back to raw
    normalised_customers = local_bucket / "normalised" / "customers.json"
    if normalised_customers.is_file():
        customers_path = normalised_customers
        log.info("  Using normalised customers from Step 2: %s", customers_path)
    else:
        customers_path = records_dir / "customers.json"
        log.warning(
            "  Normalised customers not found — using raw: %s", customers_path
        )
        log.warning("  Tip: Run Step 2 first → python scripts/step2_metadata_normalisation.py")

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 4: Entity Resolution")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Determine which records refer to the same real-world entity.")
    log.info("WHY     : Prevents duplicate customer nodes in the A-Box RDF graph.")
    log.info("          Strong ID matching is safe; fuzzy names only flag for review.")
    log.info("          (Architecture example: 'Ravi', 'R. Kumar', 'Ravi Kumar')")
    log.info("THRESHOLD: %.0f%% name similarity triggers a review candidate flag", args.threshold)
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Customers file : %s", customers_path.resolve())
    log.info("  File exists    : %s", customers_path.is_file())
    log.info("────────────────────────────────────────────────────────────────────")

    if not customers_path.is_file():
        log.error("FATAL: Customers file not found at: %s", customers_path)
        sys.exit(1)

    customers = json.loads(customers_path.read_text(encoding="utf-8"))
    log.info("  Loaded %d customer records", len(customers))

    # ── Run ER ────────────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Running Entity Resolution ...")
    log.info("  Phase 1: Strong ID matching (customer_id key) ...")
    log.info("  Phase 2: Fuzzy name scan (threshold=%d%%) ...", args.threshold)

    er_result = resolve_customers(customers, threshold=args.threshold)

    # ── Persist ───────────────────────────────────────────────────────────────
    out_path = write_local(
        "knowledge/entity_resolution/results.json",
        json.dumps(er_result, indent=2).encode(),
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    n_canonical  = len(er_result["canonical_entities"])
    n_decisions  = len(er_result["decisions"])
    n_candidates = len(er_result["review_candidates"])

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  Canonical entities formed : %d", n_canonical)
    log.info("  Identity decisions logged : %d", n_decisions)
    log.info("  Review candidates flagged : %d", n_candidates)
    if n_candidates > 0:
        log.warning("")
        log.warning("  ⚠ REVIEW REQUIRED for the following candidate pairs:")
        for c in er_result["review_candidates"]:
            log.warning(
                "    '%s' (%s) ↔ '%s' (%s) — similarity=%d%%",
                c["left_name"], c["left_id"], c["right_name"], c["right_id"], c["name_similarity"],
            )
        log.warning("")
        log.warning(
            "  Action: Inspect results.json and update SEMANTIC_MAPPING_TABLE if merge is confirmed."
        )
    else:
        log.info("  ✓ No fuzzy review candidates — all identities are unambiguous.")
    log.info("  ER results file    : %s", Path(out_path).resolve())
    log.info("  AWS equivalent     : s3://<bucket>/knowledge/entity_resolution/results.json")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 4 COMPLETE — Entity resolution results written.")
    log.info("   Next step → Run: python scripts/step5_abox_rdf_generation.py")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
