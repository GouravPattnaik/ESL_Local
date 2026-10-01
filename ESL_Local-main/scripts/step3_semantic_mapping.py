"""
step3_semantic_mapping.py
==========================
ESA Pipeline — Step 3: Semantic Mapping

PURPOSE
-------
This script is Step 3 in the ESA pipeline. In the architecture it corresponds to:

  Normalised Metadata (from Step 2)
      → Semantic Mapping (Lambda / LLM)
      → Approved Mappings (field → ontology IRI)
      → A-Box RDF Generation (Step 5)

Locally we:
  1. Load the normalised records from local_bucket/normalised/ (from Step 2).
  2. Load the T-Box ontology from local_bucket/ontology/ (from Step 1).
  3. Apply a field-to-ontology mapping table:
       e.g. customer_id → ex:customerId (xsd:string)
            credit_score → ex:creditScore (xsd:integer)
  4. Produce an "Approved Mapping Report" as JSON that logs every field,
     its mapped ontology IRI, its XSD datatype, and mapping confidence.
  5. Flag any unmapped fields for human review (simulates LLM candidate + human review).
  6. Write approved mappings to local_bucket/semantic_mapping/approved_mappings.json

WHY SEMANTIC MAPPING?
---------------------
Semantic mapping is what makes this "semantic" analytics — it's the bridge between
raw business data and the shared vocabulary (ontology). Without this step, the A-Box
RDF would just be arbitrary triples with no connection to the T-Box classes/properties.

A-Box generation (Step 5) reads approved_mappings.json to know which ontology
properties to use when creating RDF triples — making the process transparent and auditable.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                                AWS
  ──────────────────────────────────────────────────────────────────────
  local_bucket/normalised/*.json     → S3 (Normalised Metadata)
  MAPPING_TABLE (this file)          → Lambda: Semantic Mapping (LLM + rules)
  approved_mappings.json             → Approved Mappings artefact in S3
  Unmapped fields flagged in log     → Human Review step (optional Jira ticket)

INPUTS
------
  - local_bucket/normalised/customers.json  (from Step 2)
  - local_bucket/normalised/accounts.json   (from Step 2)
  - local_bucket/normalised/loans.json      (from Step 2)
  - local_bucket/ontology/centree/ontology.nq (from Step 1)

OUTPUTS
-------
  - local_bucket/semantic_mapping/approved_mappings.json
  - Console log showing each field → ontology IRI mapping decision

RUN
---
  python scripts/step3_semantic_mapping.py
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# ── Path bootstrap ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

# ── Logging setup ─────────────────────────────────────────────────────────────
from _logging_setup import setup_logging
_log_file = setup_logging("step3_semantic_mapping", _ROOT)
log = logging.getLogger("step3.semantic_mapping")


# ── Mapping table ─────────────────────────────────────────────────────────────
# Format: canonical_field → {iri, datatype, entity, confidence, method}
#   confidence: 1.0 = rule-based (certain), 0.9+ = LLM-suggested + human approved
#   method: "rule" | "llm_suggested" | "human_review"
EX = "https://example.org/esa/"

SEMANTIC_MAPPING_TABLE = {
    # Customer fields
    "customer_id": {
        "iri":        EX + "customerId",
        "datatype":   "xsd:string",
        "entity":     "Customer",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Stable unique identifier for a customer entity.",
    },
    "full_name": {
        "iri":        EX + "fullName",
        "datatype":   "xsd:string",
        "entity":     "Customer",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Legal full name. Maps to ex:fullName — required by SHACL shape.",
    },
    "email": {
        "iri":        EX + "email",
        "datatype":   "xsd:string",
        "entity":     "Customer",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Primary contact email address.",
    },
    "phone": {
        "iri":        EX + "phone",
        "datatype":   "xsd:string",
        "entity":     "Customer",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Primary contact phone number.",
    },
    "credit_score": {
        "iri":        EX + "creditScore",
        "datatype":   "xsd:integer",
        "entity":     "Customer",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Credit bureau score; integer range typically 300–900.",
    },
    "branch_id": {
        "iri":        EX + "registeredAt",
        "datatype":   "IRI",   # this is an object property → creates a node
        "entity":     "Customer→Branch",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Links Customer to Branch node via ex:registeredAt object property.",
    },
    # Account fields
    "account_id": {
        "iri":        EX + "accountId",
        "datatype":   "xsd:string",
        "entity":     "Account",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Unique account identifier.",
    },
    "account_type": {
        "iri":        EX + "accountType",
        "datatype":   "xsd:string",
        "entity":     "Account",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Type of account (e.g. Savings, Current, Fixed Deposit).",
    },
    "balance": {
        "iri":        EX + "balance",
        "datatype":   "xsd:decimal",
        "entity":     "Account",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Account balance in base currency (INR). Decimal precision.",
    },
    "status": {
        "iri":        EX + "accountStatus",
        "datatype":   "xsd:string",
        "entity":     "Account|Loan",
        "confidence": 0.95,
        "method":     "llm_suggested",
        "note":       "Status field shared by Account and Loan — context-disambiguated in A-Box.",
    },
    # Loan fields
    "loan_id": {
        "iri":        EX + "loanId",
        "datatype":   "xsd:string",
        "entity":     "Loan",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Unique loan identifier.",
    },
    "loan_type": {
        "iri":        EX + "loanType",
        "datatype":   "xsd:string",
        "entity":     "Loan",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Type of loan (e.g. Home Loan, Personal, Education).",
    },
    "amount": {
        "iri":        EX + "loanAmount",
        "datatype":   "xsd:decimal",
        "entity":     "Loan",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Principal loan amount in INR.",
    },
    "interest_rate": {
        "iri":        EX + "interestRate",
        "datatype":   "xsd:decimal",
        "entity":     "Loan",
        "confidence": 1.0,
        "method":     "rule",
        "note":       "Annual interest rate as a decimal (e.g. 8.5 for 8.5%).",
    },
}


def collect_all_fields(records_dir: Path) -> dict[str, set]:
    """
    Discover all unique fields present in each normalised JSON file.

    Input : directory with normalised JSON files
    Output: dict {entity: set_of_field_names}
    """
    tables = {}
    for fname in ("customers.json", "accounts.json", "loans.json"):
        path = records_dir / fname
        entity = fname.replace(".json", "")
        if not path.is_file():
            tables[entity] = set()
            log.warning("[FieldScan][%s] File not found: %s", entity, path)
            continue
        records = json.loads(path.read_text(encoding="utf-8"))
        fields = set()
        for row in records:
            fields.update(row.keys())
        tables[entity] = fields
        log.info("[FieldScan][%s] Discovered %d unique fields: %s",
                 entity, len(fields), sorted(fields))
    return tables


def apply_mapping(all_fields: dict[str, set]) -> tuple[list, list]:
    """
    Apply the semantic mapping table to discovered fields.

    Input : {entity: set_of_fields}
    Output: (approved_mappings list, unmapped_fields list)
    """
    approved = []
    unmapped = []

    all_field_names = set()
    for fields in all_fields.values():
        all_field_names.update(fields)

    log.info("[Mapping] Applying semantic mapping table to %d unique fields ...", len(all_field_names))

    for field in sorted(all_field_names):
        if field in SEMANTIC_MAPPING_TABLE:
            entry = SEMANTIC_MAPPING_TABLE[field]
            approved.append({
                "field":      field,
                "iri":        entry["iri"],
                "datatype":   entry["datatype"],
                "entity":     entry["entity"],
                "confidence": entry["confidence"],
                "method":     entry["method"],
                "note":       entry["note"],
            })
            confidence_pct = int(entry["confidence"] * 100)
            log.info(
                "[Mapping]  ✓ %-20s → %-55s [%s] (%d%%)",
                field, entry["iri"], entry["method"], confidence_pct,
            )
        else:
            unmapped.append({"field": field, "action": "human_review_required"})
            log.warning(
                "[Mapping]  ✗ %-20s → UNMAPPED — flagged for human review",
                field,
            )

    return approved, unmapped


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from esa.config import setting
    from esa.storage import write_local

    local_bucket = Path(setting("LOCAL_BUCKET_DIR", "local_bucket"))
    normalised_dir = local_bucket / "normalised"

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 3: Semantic Mapping")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Map each normalised field to its ontology IRI and XSD datatype.")
    log.info("WHY     : This is the core of 'semantic' analytics — connecting raw")
    log.info("          business data fields to a shared ontology vocabulary (T-Box).")
    log.info("          A-Box RDF generation (Step 5) reads these approved mappings.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Normalised records dir : %s", normalised_dir.resolve())
    log.info("  Mapping source         : SEMANTIC_MAPPING_TABLE (hard-coded rules +")
    log.info("                           LLM-suggested for ambiguous fields)")
    log.info("────────────────────────────────────────────────────────────────────")

    if not normalised_dir.is_dir():
        log.error("Normalised records not found at: %s", normalised_dir.resolve())
        log.error("Fix: Run Step 2 first → python scripts/step2_metadata_normalisation.py")
        sys.exit(1)

    # ── Discover fields ───────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Scanning normalised records for fields ...")
    all_fields = collect_all_fields(normalised_dir)

    # ── Apply semantic mapping ────────────────────────────────────────────────
    log.info("")
    log.info("▶ Applying semantic mapping table ...")
    approved, unmapped = apply_mapping(all_fields)

    # ── Build report ──────────────────────────────────────────────────────────
    report = {
        "ontology_namespace": EX,
        "total_fields_scanned": sum(len(v) for v in all_fields.values()),
        "approved_mappings":    approved,
        "unmapped_fields":      unmapped,
        "mapping_coverage_pct": (
            round(len(approved) / max(len(approved) + len(unmapped), 1) * 100, 1)
        ),
    }

    out_path = write_local(
        "semantic_mapping/approved_mappings.json",
        json.dumps(report, indent=2).encode(),
    )

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  Approved mappings    : %d fields mapped", len(approved))
    log.info("  Unmapped (review)    : %d fields need human review", len(unmapped))
    log.info("  Coverage             : %.1f%%", report["mapping_coverage_pct"])
    log.info("  Mappings file        : %s", Path(out_path).resolve())
    log.info("  AWS equivalent       : s3://<bucket>/semantic_mapping/approved_mappings.json")
    log.info("────────────────────────────────────────────────────────────────────")
    if unmapped:
        log.warning("  ⚠ Unmapped fields: %s", [f["field"] for f in unmapped])
        log.warning("    → Review and add to SEMANTIC_MAPPING_TABLE before Step 5.")
    log.info("")
    log.info("✅ Step 3 COMPLETE — Semantic mappings approved and saved.")
    log.info("   Next step → Run: python scripts/step4_entity_resolution.py")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
