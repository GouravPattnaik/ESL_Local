"""
step2_metadata_normalisation.py
================================
ESA Pipeline — Step 2: Metadata Normalisation

PURPOSE
-------
This script is Step 2 in the ESA pipeline. In the architecture it corresponds to:

  Databricks (structured data)
      → Metadata Extraction (AWS Glue / Lambda)
      → Collibra (business glossary / data dictionary)
      → Metadata Normalisation (Lambda)
      → Normalized Metadata (ready for Semantic Mapping)

Locally we:
  1. Load raw structured records (customers, accounts, loans) from JSON files
     (simulating what AWS Glue / Databricks would extract from tables/views).
  2. Standardise field names to canonical snake_case.
  3. Normalise data types (e.g. amounts to float, IDs to str, names to title-case).
  4. Resolve synonyms (e.g. "cust_num" → "customer_id", "amt" → "amount").
  5. Validate required business fields (completeness check like Collibra would enforce).
  6. Write normalised records to local_bucket/normalised/ as JSON.

WHY NORMALISE BEFORE SEMANTIC MAPPING?
---------------------------------------
Raw source data has inconsistent field names, mixed types, and missing values.
Semantic Mapping (Step 3) maps normalised fields to ontology IRIs.
If you skip normalisation, the mapping fails silently because field names don't match.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                              AWS
  ──────────────────────────────────────────────────────────────────
  data/records/customers.json      → Databricks tables / Glue catalogue
  data/records/accounts.json       → Databricks tables / Glue catalogue
  data/records/loans.json          → Databricks tables / Glue catalogue
  Field-name standardisation       → Lambda: Metadata Normalisation
  Type coercion / synonym resolve  → Collibra API for business glossary terms
  local_bucket/normalised/*.json   → S3 (Normalised Metadata zone)

INPUTS
------
  - STRUCTURED_DATA_PATH (.env) : Directory with customers.json, accounts.json, loans.json

OUTPUTS
-------
  - local_bucket/normalised/customers.json
  - local_bucket/normalised/accounts.json
  - local_bucket/normalised/loans.json
  - Console log showing per-table field mappings and row counts

RUN
---
  python scripts/step2_metadata_normalisation.py
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
_log_file = setup_logging("step2_metadata_norm", _ROOT)
log = logging.getLogger("step2.metadata_normalisation")


# ── Field synonym maps (simulates Collibra business glossary lookup) ──────────
# Maps: raw source field names → canonical normalised field names
CUSTOMER_FIELD_MAP = {
    "cust_id":       "customer_id",
    "cust_num":      "customer_id",
    "customer_num":  "customer_id",
    "name":          "full_name",
    "customer_name": "full_name",
    "mail":          "email",
    "phone_number":  "phone",
    "credit":        "credit_score",
    "score":         "credit_score",
    "branch":        "branch_id",
    "branch_code":   "branch_id",
    # canonical names pass through unchanged:
    "customer_id":   "customer_id",
    "full_name":     "full_name",
    "email":         "email",
    "phone":         "phone",
    "credit_score":  "credit_score",
    "branch_id":     "branch_id",
}

ACCOUNT_FIELD_MAP = {
    "acc_id":        "account_id",
    "account_num":   "account_id",
    "cust_id":       "customer_id",
    "type":          "account_type",
    "acct_type":     "account_type",
    "bal":           "balance",
    "acct_balance":  "balance",
    "state":         "status",
    # canonical pass-through:
    "account_id":    "account_id",
    "customer_id":   "customer_id",
    "account_type":  "account_type",
    "balance":       "balance",
    "status":        "status",
}

LOAN_FIELD_MAP = {
    "loan_num":      "loan_id",
    "cust_id":       "customer_id",
    "acc_id":        "account_id",
    "type":          "loan_type",
    "loan_amt":      "amount",
    "amt":           "amount",
    "rate":          "interest_rate",
    "ir":            "interest_rate",
    "state":         "status",
    # canonical pass-through:
    "loan_id":       "loan_id",
    "customer_id":   "customer_id",
    "account_id":    "account_id",
    "loan_type":     "loan_type",
    "amount":        "amount",
    "interest_rate": "interest_rate",
    "status":        "status",
}

# Required fields per entity (Collibra-style data quality rules)
REQUIRED_FIELDS = {
    "customers": ["customer_id", "full_name"],
    "accounts":  ["account_id", "customer_id"],
    "loans":     ["loan_id", "customer_id"],
}


def normalise_record(raw: dict, field_map: dict, entity: str, idx: int) -> dict:
    """
    Normalise a single raw record:
      1. Rename fields using synonym map.
      2. Coerce types (IDs→str, amounts→float, names→title-case).
      3. Strip whitespace from strings.

    Input : raw dict, field_map for synonyms, entity label, row index
    Output: normalised dict
    """
    norm = {}
    renamed = []

    for raw_key, raw_val in raw.items():
        canonical = field_map.get(raw_key.strip().lower())
        if canonical is None:
            log.debug(
                "[Normalise][%s] Row %d: Unknown field '%s' — keeping as-is",
                entity, idx, raw_key,
            )
            canonical = raw_key  # keep unknown fields unchanged
        if canonical != raw_key:
            renamed.append(f"{raw_key}→{canonical}")
        norm[canonical] = raw_val

    if renamed:
        log.info(
            "[Normalise][%s] Row %d: Renamed fields: %s", entity, idx, ", ".join(renamed)
        )

    # Type coercions
    for id_field in ("customer_id", "account_id", "loan_id", "branch_id"):
        if id_field in norm and norm[id_field] is not None:
            norm[id_field] = str(norm[id_field]).strip()

    for float_field in ("balance", "amount", "interest_rate"):
        if float_field in norm and norm[float_field] is not None:
            try:
                norm[float_field] = float(norm[float_field])
            except (ValueError, TypeError):
                log.warning(
                    "[Normalise][%s] Row %d: Cannot coerce '%s'=%r to float — setting None",
                    entity, idx, float_field, norm[float_field],
                )
                norm[float_field] = None

    for int_field in ("credit_score",):
        if int_field in norm and norm[int_field] is not None:
            try:
                norm[int_field] = int(norm[int_field])
            except (ValueError, TypeError):
                log.warning(
                    "[Normalise][%s] Row %d: Cannot coerce '%s'=%r to int — setting None",
                    entity, idx, int_field, norm[int_field],
                )
                norm[int_field] = None

    for str_field in ("full_name",):
        if str_field in norm and isinstance(norm[str_field], str):
            norm[str_field] = norm[str_field].strip().title()

    return norm


def normalise_table(
    records: list[dict],
    field_map: dict,
    entity: str,
    required: list[str],
) -> tuple[list[dict], int]:
    """
    Normalise all records in a table and validate required fields.

    Input : raw records list, field_map, entity label, required field list
    Output: (normalised records list, invalid row count)
    """
    log.info("[Normalise][%s] Input: %d raw records", entity, len(records))
    normalised = []
    invalid = 0

    for idx, row in enumerate(records):
        norm = normalise_record(row, field_map, entity, idx)

        # Validate required fields
        missing = [f for f in required if not norm.get(f)]
        if missing:
            log.warning(
                "[Normalise][%s] Row %d INVALID — missing required field(s): %s | Row: %s",
                entity, idx, missing, norm,
            )
            invalid += 1
            continue

        normalised.append(norm)
        log.debug("[Normalise][%s] Row %d OK: %s", entity, idx, norm)

    log.info(
        "[Normalise][%s] Output: %d valid records | %d invalid (dropped)",
        entity, len(normalised), invalid,
    )
    return normalised, invalid


def load_json(path: Path, entity: str) -> list[dict]:
    if not path.is_file():
        log.warning("[Load][%s] File not found: %s — returning empty list", entity, path)
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    log.info("[Load][%s] Loaded %d raw records from %s", entity, len(records), path.name)
    return records


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from esa.config import setting
    from esa.storage import write_local

    records_dir = Path(setting("STRUCTURED_DATA_PATH", "data/records"))

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 2: Metadata Normalisation")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Standardise raw field names, coerce types, and validate")
    log.info("          required fields before semantic mapping to the ontology.")
    log.info("WHY     : Semantic Mapping (Step 3) requires canonical field names.")
    log.info("          Collibra provides the business glossary; we simulate its")
    log.info("          synonym lookups via the FIELD_MAP dictionaries above.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  Records directory : %s", records_dir.resolve())
    log.info("────────────────────────────────────────────────────────────────────")

    # ── Load raw data ─────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Loading raw source records ...")
    customers_raw = load_json(records_dir / "customers.json", "customers")
    accounts_raw  = load_json(records_dir / "accounts.json",  "accounts")
    loans_raw     = load_json(records_dir / "loans.json",     "loans")

    # ── Normalise ─────────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Normalising Customer records ...")
    customers_norm, cust_invalid = normalise_table(
        customers_raw, CUSTOMER_FIELD_MAP, "customers", REQUIRED_FIELDS["customers"]
    )

    log.info("")
    log.info("▶ Normalising Account records ...")
    accounts_norm, acc_invalid = normalise_table(
        accounts_raw, ACCOUNT_FIELD_MAP, "accounts", REQUIRED_FIELDS["accounts"]
    )

    log.info("")
    log.info("▶ Normalising Loan records ...")
    loans_norm, loan_invalid = normalise_table(
        loans_raw, LOAN_FIELD_MAP, "loans", REQUIRED_FIELDS["loans"]
    )

    # ── Persist ───────────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Writing normalised records to local bucket ...")
    cust_path = write_local("normalised/customers.json", json.dumps(customers_norm, indent=2).encode())
    acc_path  = write_local("normalised/accounts.json",  json.dumps(accounts_norm,  indent=2).encode())
    loan_path = write_local("normalised/loans.json",     json.dumps(loans_norm,     indent=2).encode())

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    log.info("  Customers  : %d records → %s", len(customers_norm), Path(cust_path).resolve())
    log.info("  Accounts   : %d records → %s", len(accounts_norm),  Path(acc_path).resolve())
    log.info("  Loans      : %d records → %s", len(loans_norm),     Path(loan_path).resolve())
    log.info("  Total invalid rows dropped: %d", cust_invalid + acc_invalid + loan_invalid)
    log.info("  AWS equivalent: s3://<bucket>/normalised/")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 2 COMPLETE — Metadata normalised and written to local bucket.")
    log.info("   Next step → Run: python scripts/step3_semantic_mapping.py")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
