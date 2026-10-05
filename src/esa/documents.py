"""
documents.py — Document text extraction and chunking.

Architecture alignment:
  - Corresponds to 'Doc Processing (Bedrock / local)' step in the architecture.
  - Extracts text from unstructured documents (PDF, DOCX, TXT, MD).
  - Splits text into overlapping chunks ready for embedding.

Input : directory path containing documents
Output: list of chunk dicts {id, text, source, chunk}
"""

import logging
from pathlib import Path
from pypdf import PdfReader
from docx import Document

log = logging.getLogger(__name__)

SUPPORTED = {".txt", ".md", ".pdf", ".docx"}


def extract_text(path: Path) -> str:
    """
    Input : Path to a single document file.
    Output: Extracted plain text string.
    """
    suffix = path.suffix.lower()
    log.debug("[DocProcessing]   Extracting text from: %s (type=%s)", path.name, suffix)
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        log.debug("[DocProcessing]   PDF pages read: %d", len(reader.pages))
        return text
    if suffix == ".docx":
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    return ""


def load_chunks(directory: str, chunk_chars: int = 900, overlap: int = 120) -> list[dict]:
    """
    Walk a directory, extract text from all supported documents, and split
    into overlapping chunks for vector embedding.

    Input : directory path (str), optional chunk size and overlap in characters.
    Output: list of chunk dicts — each has keys: id, text, source, chunk.
    """
    base = Path(directory)
    log.info("[DocProcessing] Input: scanning directory → %s", base.resolve())
    log.info("[DocProcessing] Chunk size: %d chars | Overlap: %d chars", chunk_chars, overlap)

    all_files = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED]
    log.info("[DocProcessing] Found %d supported document(s): %s", len(all_files), [f.name for f in all_files])

    chunks = []
    for path in all_files:
        text = extract_text(path).strip()
        if not text:
            log.warning("[DocProcessing]   No text extracted from: %s — skipping", path.name)
            continue

        step = max(1, chunk_chars - overlap)
        file_chunks = []
        for idx, start in enumerate(range(0, len(text), step)):
            piece = text[start:start + chunk_chars].strip()
            if piece:
                file_chunks.append({"id": f"{path.stem}-{idx}", "text": piece,
                                    "source": str(path), "chunk": idx})

        log.info("[DocProcessing]   %s → %d chunk(s) created", path.name, len(file_chunks))
        chunks.extend(file_chunks)

    log.info("[DocProcessing] Output: %d total chunks ready for embedding", len(chunks))
    return chunks
