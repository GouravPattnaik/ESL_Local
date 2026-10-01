from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from esa.vectorstore import answer_query
query = " ".join(sys.argv[1:]) or "Summarize the documents"
print(answer_query(query))
