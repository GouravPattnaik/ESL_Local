from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from esa.documents import load_chunks
from esa.vectorstore import index_chunks
from esa.config import setting
chunks = load_chunks(setting("DOCS_DIR", "data/documents"))
print(f"Indexed {index_chunks(chunks)} chunks")
