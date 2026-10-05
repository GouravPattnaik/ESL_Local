import sys
import logging
from pathlib import Path

# Configure logging to print to the console
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from esa.pipeline import run_all

if __name__ == "__main__":
    run_all()
