"""
run_pipeline.py
================
ESA Pipeline — Full Orchestrator (Steps 1–7 in sequence)

PURPOSE
-------
Runs all ESA pipeline steps in the correct order with a single command.
Each step logs its INPUT, OUTPUT, and WHY to console + timestamped log files.

STEPS
-----
  Step 1: Ontology / T-Box ingestion
  Step 2: Metadata Normalisation
  Step 3: Semantic Mapping
  Step 4: Entity Resolution
  Step 5: A-Box RDF Generation + SHACL Validation
  Step 6: Document Processing + Vector Store Indexing
  Step 7: Graph Load (Neo4j / Neptune) — optional

RUN
---
  python scripts/run_pipeline.py
  python scripts/run_pipeline.py --skip-graph-load  (skip Neo4j if not running)
  python scripts/run_pipeline.py --validate-shacl   (enable SHACL in Step 5)
  python scripts/run_pipeline.py --steps 1 2 3      (run only specific steps)
"""

import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# ── Path bootstrap ────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
_SCRIPTS = Path(__file__).resolve().parent

# ── Logging setup ─────────────────────────────────────────────────────────────
from _logging_setup import setup_logging
_log_file = setup_logging("run_pipeline", _ROOT)
log = logging.getLogger("pipeline.orchestrator")

STEP_SCRIPTS = {
    1: ("step1_ontology_tbox.py",        "Ontology / T-Box Ingestion"),
    2: ("step2_metadata_normalisation.py", "Metadata Normalisation"),
    3: ("step3_semantic_mapping.py",      "Semantic Mapping"),
    4: ("step4_entity_resolution.py",     "Entity Resolution"),
    5: ("step5_abox_rdf_generation.py",  "A-Box RDF Generation + SHACL"),
    6: ("step6_document_processing.py",  "Document Processing + Vector Store"),
    7: ("step7_graph_load.py",           "Graph Load (Neo4j / Neptune)"),
}


def run_step(step_num: int, extra_args: list[str] = None) -> bool:
    """
    Run a single pipeline step script as a subprocess.
    Returns True if the step succeeded (exit code 0), False otherwise.
    """
    script_name, step_label = STEP_SCRIPTS[step_num]
    script_path = _SCRIPTS / script_name

    if not script_path.is_file():
        log.error("  Script not found: %s", script_path)
        return False

    cmd = [sys.executable, str(script_path)] + (extra_args or [])
    log.info("  Running: %s", " ".join(cmd))

    result = subprocess.run(cmd, cwd=str(_ROOT))
    if result.returncode != 0:
        log.error("  Step %d FAILED (exit code: %d)", step_num, result.returncode)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ESA Full Pipeline Orchestrator — runs Steps 1–7 in sequence"
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        type=int,
        choices=list(STEP_SCRIPTS.keys()),
        default=None,
        help="Run only specific steps (e.g. --steps 1 2 5). Default: all steps.",
    )
    parser.add_argument(
        "--skip-graph-load",
        action="store_true",
        help="Skip Step 7 (graph load) — useful if Neo4j is not running.",
    )
    parser.add_argument(
        "--validate-shacl",
        action="store_true",
        help="Enable SHACL validation in Step 5.",
    )
    parser.add_argument(
        "--dry-run-graph",
        action="store_true",
        help="Run Step 7 in dry-run mode (show queries without connecting to Neo4j).",
    )
    args = parser.parse_args()

    steps_to_run = args.steps or list(STEP_SCRIPTS.keys())
    if args.skip_graph_load and 7 in steps_to_run:
        steps_to_run.remove(7)

    log.info("╔══════════════════════════════════════════════════════════════════════╗")
    log.info("║   ESA — Enterprise Semantic Analytics  Full Pipeline Orchestrator   ║")
    log.info("╚══════════════════════════════════════════════════════════════════════╝")
    log.info("")
    log.info("  Architecture: CENtree → Lambda → S3 → Neptune + OpenSearch + RAG")
    log.info("  Local POC   : Turtle → PyOxigraph → local_bucket → ChromaDB → Neo4j")
    log.info("")
    log.info("  Steps to run: %s", steps_to_run)
    log.info("  Master log  : %s", _log_file)
    log.info("")

    results = {}
    t_start = datetime.now()

    for step_num in steps_to_run:
        step_label = STEP_SCRIPTS[step_num][1]
        log.info("━" * 72)
        log.info("  ▶ STEP %d / %d : %s", step_num, max(steps_to_run), step_label)
        log.info("━" * 72)

        extra_args = []
        if step_num == 5 and args.validate_shacl:
            extra_args.append("--validate-shacl")
        if step_num == 7 and args.dry_run_graph:
            extra_args.append("--dry-run")

        t_step = datetime.now()
        success = run_step(step_num, extra_args)
        elapsed = (datetime.now() - t_step).total_seconds()

        results[step_num] = {
            "label":   step_label,
            "success": success,
            "elapsed": f"{elapsed:.1f}s",
        }

        if not success:
            log.error("")
            log.error("  ✗ Step %d FAILED — stopping pipeline.", step_num)
            log.error("  Fix the error above then re-run from this step:")
            log.error("    python scripts/run_pipeline.py --steps %d", step_num)
            break

        log.info("  ✓ Step %d complete in %s", step_num, results[step_num]["elapsed"])
        log.info("")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = (datetime.now() - t_start).total_seconds()
    log.info("")
    log.info("╔══════════════════════════════════════════════════════════════════════╗")
    log.info("║   PIPELINE SUMMARY                                                   ║")
    log.info("╠══════════════════════════════════════════════════════════════════════╣")
    all_passed = True
    for step_num, info in results.items():
        status = "✓ PASSED" if info["success"] else "✗ FAILED"
        log.info("║  Step %d: %-42s %s  %s  ║",
                 step_num, info["label"], status, info["elapsed"].rjust(6))
        if not info["success"]:
            all_passed = False
    log.info("║                                                                      ║")
    log.info("║  Total time: %-54s  ║", f"{total_elapsed:.1f}s")
    log.info("╠══════════════════════════════════════════════════════════════════════╣")

    if all_passed:
        log.info("║  ✅ ALL STEPS PASSED — Knowledge graph ready!                        ║")
        log.info("╠══════════════════════════════════════════════════════════════════════╣")
        log.info("║  Next: Query the pipeline —                                          ║")
        log.info("║    python scripts/step8_query_rag.py \"your question here\"            ║")
        log.info("║    Explore graph: http://localhost:7474 (Neo4j Browser)               ║")
    else:
        log.info("║  ✗ PIPELINE INCOMPLETE — see errors above                             ║")
        log.info("║    Re-run individual steps to fix: python scripts/stepN_xxx.py        ║")

    log.info("╠══════════════════════════════════════════════════════════════════════╣")
    log.info("║  Outputs in: local_bucket/                                           ║")
    log.info("║    ontology/centree/ontology.nq                                      ║")
    log.info("║    normalised/customers.json, accounts.json, loans.json              ║")
    log.info("║    semantic_mapping/approved_mappings.json                           ║")
    log.info("║    knowledge/entity_resolution/results.json                          ║")
    log.info("║    knowledge/abox/instances.nq                                       ║")
    log.info("║    vectorstore/chroma/                                               ║")
    log.info("╠══════════════════════════════════════════════════════════════════════╣")
    log.info("║  Master log: %-54s  ║", str(_log_file.name))
    log.info("╚══════════════════════════════════════════════════════════════════════╝")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
