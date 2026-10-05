"""
entity_resolution.py — Conservative entity matching demo.

Architecture alignment:
  - Corresponds to the 'Entity Resolution (Lambda / ML)' step in the architecture.
  - Uses strong identifier matching (customer_id) as the primary key.
  - Fuzzy name similarity (RapidFuzz) is used ONLY to propose manual-review candidates.
  - Never auto-merges records based solely on name similarity.

Input : list of customer dicts (from the structured records source)
Output: dict with canonical_entities, decisions, and review_candidates
"""

import logging
from rapidfuzz import fuzz

log = logging.getLogger(__name__)


def resolve_customers(records: list[dict], threshold: int = 96) -> dict:
    """
    Input : Customer records list — each dict must have 'customer_id' and 'full_name'.
    Output: {
        canonical_entities: list of resolved canonical records,
        decisions:          list of per-record identity decisions,
        review_candidates:  list of fuzzy name-match pairs flagged for manual review
    }
    """
    log.info("[EntityResolution] Input: %d customer records to process", len(records))

    canonical: dict = {}
    decisions: list = []

    for row in records:
        cid  = str(row.get("customer_id", "")).strip()
        name = str(row.get("full_name",   "")).strip()
        if not cid:
            log.warning("[EntityResolution]   Skipping row — no customer_id: %s", row)
            continue

        key = "CUSTOMER:" + cid
        canonical.setdefault(key, {"customer_id": cid, "full_name": name, "source_ids": []})
        canonical[key]["source_ids"].append(cid)
        decisions.append({"customer_id": cid, "canonical_id": key, "decision": "same_verified_id"})
        log.debug("[EntityResolution]   Resolved %s → canonical key '%s'", cid, key)

    log.info("[EntityResolution] Canonical entities formed: %d", len(canonical))

    # ── Fuzzy name similarity scan (candidate generation only) ────────────────
    log.info("[EntityResolution] Scanning for fuzzy name-match candidates (threshold=%d%%) ...", threshold)
    candidates: list = []
    for i, left in enumerate(records):
        for right in records[i + 1:]:
            if left.get("customer_id") == right.get("customer_id"):
                continue
            a = str(left.get("full_name",  ""))
            b = str(right.get("full_name", ""))
            score = fuzz.token_sort_ratio(a, b) if a and b else 0
            if score >= threshold:
                candidates.append({
                    "left_id":         left.get("customer_id"),
                    "right_id":        right.get("customer_id"),
                    "left_name":       a,
                    "right_name":      b,
                    "name_similarity": score,
                    "action":          "manual_review"
                })
                log.warning(
                    "[EntityResolution]   CANDIDATE: '%s' (%s) ↔ '%s' (%s) — similarity=%d%% → flagged for MANUAL REVIEW",
                    a, left.get("customer_id"), b, right.get("customer_id"), score
                )

    if not candidates:
        log.info("[EntityResolution] No fuzzy name-match candidates found.")

    result = {
        "canonical_entities":  list(canonical.values()),
        "decisions":           decisions,
        "review_candidates":   candidates
    }
    log.info(
        "[EntityResolution] Output: %d canonical entities | %d decisions | %d review candidates",
        len(result["canonical_entities"]), len(result["decisions"]), len(result["review_candidates"])
    )
    return result
