"""
_logging_setup.py
==================
Shared logging bootstrap for all ESA pipeline step scripts.

Provides setup_logging() which:
  - Reconfigures stdout to UTF-8 so Unicode symbols (arrows, checkmarks) render on Windows
  - Writes to both console (stdout) and a timestamped log file in logs/
  - Returns the log file path for the caller to report
"""

import sys
import logging
from pathlib import Path
from datetime import datetime


def setup_logging(step_name: str, project_root: Path) -> Path:
    """
    Configure logging for a pipeline step.

    Args:
        step_name  : short name used in log filename (e.g. 'step1_ontology')
        project_root: absolute path to repo root (for logs/ directory)

    Returns:
        Path to the log file created.
    """
    # Ensure stdout uses UTF-8 on Windows (avoids cp1252 UnicodeEncodeError)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{step_name}_{ts}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s -- %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return log_file
