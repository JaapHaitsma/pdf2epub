from __future__ import annotations

from pathlib import Path
from typing import List


def extract_text_by_pages(pdf_path: Path) -> List[str]:
    """Extract text from a PDF file, page by page.

    Requires pdfminer.six. If not available, raises an informative error.
    """
    try:
        from pdfminer.high_level import extract_text
        from pdfminer.pdfpage import PDFPage
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "pdfminer.six is required for text extraction. Install it with 'uv add pdfminer.six'."
        ) from e

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    pages_text: List[str] = []
    with open(pdf_path, "rb") as f:
        for _i, _page in enumerate(PDFPage.get_pages(f)):
            # pdfminer doesn't expose page-wise extract_text directly; simplest is to
            # re-read per page number by splitting the file, but that's slow.
            # Instead, we fall back to extracting the full text once.
            # To keep it simple and performant, we extract once outside the loop.
            # (We keep the loop structure for future refinement.)
            break

    # Extract all text at once as a single string
    full_text = extract_text(str(pdf_path)) or ""

    # Basic heuristic: split on form-feed or multiple newlines to emulate pages
    # Many PDFs contain page break markers; if not, this will still produce chunks.
    import re

    candidates = re.split(r"\f+|\n{3,}", full_text)
    pages_text = [t.strip() for t in candidates if t.strip()]

    return pages_text


def chunk_text(pages: list[str], max_chars: int = 12000) -> list[str]:
    """Group page texts into chunks up to `max_chars` to stay within model limits."""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in pages:
        p = p.strip()
        if not p:
            continue
        if size + len(p) + 1 > max_chars and buf:
            chunks.append("\n\n".join(buf))
            buf = [p]
            size = len(p)
        else:
            buf.append(p)
            size += len(p) + 1
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks
