"""
step7_graph_load.py
====================
ESA Pipeline — Step 7: Graph Load (RDF → Neo4j / Neptune)

PURPOSE
-------
This script is Step 7 in the ESA pipeline. In the architecture it corresponds to:

  Amazon S3 (A-Box N-Quads + T-Box N-Quads)
      → Amazon Neptune (Enterprise Knowledge Graph)
          Path A: SPARQL Bulk Load (from S3)
          Path B: Direct Graph API (INSERT/SDK for real-time updates)

Locally we:
  1. Verify A-Box N-Quads exist (from Step 5).
  2. Load the N-Quads into Neo4j Community as a simplified property-graph view.
     (This is a LOCAL stand-in for Neptune — Neo4j is NOT Neptune-compatible.)
  3. Print sample Cypher queries you can run in the Neo4j Browser.
  4. Provide the equivalent Neptune SPARQL queries for the AWS target.

WHY GRAPH LOAD AS A SEPARATE STEP?
------------------------------------
In the AWS architecture, Neptune loading is a distinct operational step:
  - T-Box (ontology) is loaded as a named graph first.
  - A-Box (instances) is bulk-loaded in N-Quads format.
  - Once in Neptune, SPARQL queries can traverse Customer→Loan→Account→Branch paths.

Keeping this as a separate step means:
  - You can re-load the graph after updating A-Box without re-running upstream steps.
  - You can verify the RDF artifact before committing to Neptune.
  - Auditors can inspect the N-Quads file independently of the graph.

NEO4J DISCLAIMER
-----------------
The local Neo4j adapter is a simplified property-graph projection —
it is NOT a Neptune RDF/SPARQL equivalent. It is useful for visual exploration
but does not support SPARQL, named graphs, or OWL reasoning.
For the AWS target, use Neptune's SPARQL endpoint with the N-Quads bulk loader.

ARCHITECTURE MAPPING (from the diagram)
----------------------------------------
  Local                                   AWS
  ──────────────────────────────────────────────────────────────────────────
  Neo4j Community (bolt://localhost:7687) → Amazon Neptune RDF/SPARQL
  local_bucket/knowledge/abox/instances.nq → S3 → Neptune Bulk Load
  Cypher queries (Neo4j Browser)          → SPARQL queries (Neptune endpoint)

INPUTS
------
  - local_bucket/knowledge/abox/instances.nq  (from Step 5)
  - NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD (.env)

OUTPUTS
-------
  - Neo4j graph populated with RDF statements as generic RDFTerm nodes
  - Console log with statement count and sample Cypher/SPARQL queries

RUN
---
  python scripts/step7_graph_load.py
  python scripts/step7_graph_load.py --nq-path custom/path/to/file.nq
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
_log_file = setup_logging("step7_graph_load", _ROOT)
log = logging.getLogger("step7.graph_load")

SAMPLE_CYPHER = """
  // Neo4j Cypher -- sample queries (run in Neo4j Browser at http://localhost:7474)
  // NOTE: These use TYPED nodes -- requires load_nquads_typed() (step7 default).

  // 1. Count all entity types
  MATCH (n) RETURN labels(n)[0] AS type, count(n) AS total ORDER BY total DESC;

  // 2. Find a specific customer and all their details
  MATCH (c:Customer {id: 'C001'})
  OPTIONAL MATCH (c)-[:OWNS_ACCOUNT]->(a:Account)
  OPTIONAL MATCH (c)-[:HAS_LOAN]->(l:Loan)
  OPTIONAL MATCH (c)-[:REGISTERED_AT]->(b:Branch)
  RETURN c.fullName, c.creditScore, collect(DISTINCT a.accountType) AS accounts,
         collect(DISTINCT l.loanType) AS loans, b.branchId;

  // 3. All customers with their credit scores (sorted high to low)
  MATCH (c:Customer)
  RETURN c.id, c.fullName, c.creditScore
  ORDER BY c.creditScore DESC;

  // 4. Customers with credit score below 700 and their loan amounts
  MATCH (c:Customer)-[:HAS_LOAN]->(l:Loan)
  WHERE c.creditScore < 700
  RETURN c.fullName, c.creditScore, l.loanType, l.loanAmount
  ORDER BY c.creditScore;

  // 5. Total loan amount per branch
  MATCH (c:Customer)-[:REGISTERED_AT]->(b:Branch)
  MATCH (c)-[:HAS_LOAN]->(l:Loan)
  RETURN b.branchId, count(l) AS loans, sum(l.loanAmount) AS total_amount
  ORDER BY total_amount DESC;

  // 6. Full graph -- visualise in Neo4j Browser (limit for readability)
  MATCH path = (c:Customer)-[*1..2]->()
  RETURN path LIMIT 50;
"""

SAMPLE_SPARQL = """
  # Neptune SPARQL — equivalent queries via SPARQL endpoint

  # Find all customers and their credit scores
  PREFIX ex: <https://example.org/esa/>
  SELECT ?customer ?name ?score WHERE {
    ?customer a ex:Customer ;
              ex:fullName ?name ;
              ex:creditScore ?score .
  } ORDER BY DESC(?score)

  # Find all loans for a specific customer
  PREFIX ex: <https://example.org/esa/>
  SELECT ?loan ?type ?amount WHERE {
    ex:customer/C001 ex:hasLoan ?loan .
    ?loan ex:loanType ?type ;
          ex:loanAmount ?amount .
  }

  # Customers with credit score < 700 and their loans
  PREFIX ex: <https://example.org/esa/>
  SELECT ?name ?score ?loanId ?amount WHERE {
    ?c a ex:Customer ;
       ex:fullName ?name ;
       ex:creditScore ?score ;
       ex:hasLoan ?loan .
    ?loan ex:loanId ?loanId ;
          ex:loanAmount ?amount .
    FILTER(?score < 700)
  }
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Step 7 — Graph Load (RDF → Neo4j / Neptune)"
    )
    parser.add_argument(
        "--nq-path",
        default=None,
        help="Override path to A-Box N-Quads file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be loaded without connecting to Neo4j.",
    )
    args = parser.parse_args()

    from esa.config import setting

    local_bucket = Path(setting("LOCAL_BUCKET_DIR", "local_bucket"))
    default_nq   = local_bucket / "knowledge" / "abox" / "instances.nq"
    nq_path      = Path(args.nq_path) if args.nq_path else default_nq

    neo4j_uri  = setting("NEO4J_URI",      "bolt://localhost:7687")
    neo4j_user = setting("NEO4J_USER",     "neo4j")
    neo4j_db   = setting("NEO4J_DATABASE", "neo4j")

    log.info("=" * 70)
    log.info("ESA PIPELINE — STEP 7: Graph Load (RDF → Neo4j / Neptune)")
    log.info("=" * 70)
    log.info("")
    log.info("PURPOSE : Load A-Box N-Quads into a graph database for SPARQL/Cypher queries.")
    log.info("WHY     : Neptune (or Neo4j locally) enables traversal queries across")
    log.info("          Customer → Account → Loan → Branch relationships that are")
    log.info("          difficult in relational databases.")
    log.info("")
    log.info("── INPUT ────────────────────────────────────────────────────────────")
    log.info("  A-Box N-Quads : %s", nq_path.resolve())
    log.info("  File exists   : %s", nq_path.is_file())
    log.info("  Neo4j URI     : %s (local stand-in for Neptune)", neo4j_uri)
    log.info("  Neo4j user    : %s", neo4j_user)
    log.info("  Neo4j DB      : %s", neo4j_db)
    log.info("  Dry run       : %s", args.dry_run)
    log.info("────────────────────────────────────────────────────────────────────")

    if not nq_path.is_file():
        log.error("FATAL: A-Box N-Quads not found at: %s", nq_path)
        log.error("Fix: Run Step 5 first → python scripts/step5_abox_rdf_generation.py")
        sys.exit(1)

    # Count triples in N-Quads for reporting
    lines = [l for l in nq_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
    log.info("  N-Quads lines (triples) to load: %d", len(lines))

    if args.dry_run:
        log.info("")
        log.info("▶ DRY RUN — showing first 5 triples that would be loaded:")
        for line in lines[:5]:
            log.info("  %s", line[:120])
        log.info("  ... (dry run complete — no Neo4j connection made)")
    else:
        # ── Load into Neo4j ───────────────────────────────────────────────────
        log.info("")
        log.info("▶ Loading N-Quads into Neo4j (typed mode) ...")
        log.info("  Connecting to: %s", neo4j_uri)
        try:
            from esa.graph_neo4j import load_nquads_typed
            count = load_nquads_typed(str(nq_path))
            log.info("  ✓ Loaded %d Cypher writes into Neo4j (typed nodes)", count)
        except Exception as exc:
            log.error("  Neo4j load FAILED: %s", exc)
            log.error("")
            log.error("  Common fixes:")
            log.error("    1. Start Neo4j: neo4j start (or via Neo4j Desktop)")
            log.error("    2. Check NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in .env")
            log.error("    3. Neo4j is OPTIONAL — the RDF N-Quads are the canonical output.")
            log.error("    4. For AWS target, use Neptune bulk load from S3 instead.")
            log.info("")
            log.info("  Proceeding to show sample queries regardless ...")
            count = 0

        # ── AWS Neptune Load (Commented out for Local POC) ────────────────────
        # To execute on AWS target environment (VPC with Neptune cluster):
        # try:
        #     from esa.graph_neptune import start_neptune_s3_load, poll_neptune_load_status
        #     s3_bucket = setting("S3_BUCKET_NAME")
        #     s3_key    = setting("RDF_OUTPUT_KEY", "knowledge/abox/instances.nq")
        #     s3_uri    = f"s3://{s3_bucket}/{s3_key}"
        #     log.info("  [AWS Neptune] Submitting bulk load from %s ...", s3_uri)
        #     res     = start_neptune_s3_load(s3_source_uri=s3_uri)
        #     load_id = res.get("payload", {}).get("loadId")
        #     poll_neptune_load_status(load_id)
        # except Exception as neptune_exc:
        #     log.error("  [AWS Neptune] Bulk load failed: %s", neptune_exc)

    # ── Sample queries ────────────────────────────────────────────────────────
    log.info("")
    log.info("▶ Sample queries for exploration:")
    log.info("")
    log.info("  ── LOCAL Neo4j Cypher (http://localhost:7474) ──────────────────")
    for line in SAMPLE_CYPHER.strip().split("\n"):
        log.info("  %s", line)
    log.info("")
    log.info("  ── AWS Neptune SPARQL (Neptune endpoint) ───────────────────────")
    for line in SAMPLE_SPARQL.strip().split("\n"):
        log.info("  %s", line)

    log.info("")
    log.info("── OUTPUT ───────────────────────────────────────────────────────────")
    if not args.dry_run:
        log.info("  Neo4j graph populated with A-Box RDF statements")
    log.info("  N-Quads canonical file : %s", nq_path.resolve())
    log.info("  AWS equivalent         : Neptune bulk load from S3:")
    log.info("    aws neptune-graph start-import-task \\")
    log.info("      --graph-identifier <graph-id> \\")
    log.info("      --source s3://<bucket>/knowledge/abox/instances.nq \\")
    log.info("      --format NQUADS")
    log.info("────────────────────────────────────────────────────────────────────")
    log.info("")
    log.info("✅ Step 7 COMPLETE — Graph load done (or dry-run shown).")
    log.info("   Explore the graph: http://localhost:7474 (Neo4j Browser)")
    log.info("   Next step → Run: python scripts/step8_query_rag.py \"your question\"")
    log.info("   Log saved → %s", _log_file)


if __name__ == "__main__":
    main()
