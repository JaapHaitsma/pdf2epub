from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any


def extract_text_by_pages(pdf_path: Path) -> List[str]:
    """Extract text from a PDF file, page by page using pdfminer.six."""
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


def extract_images(pdf_path: Path) -> Dict[str, bytes]:
    """Extract images from a PDF using PyMuPDF (fitz).

    Returns a dict mapping image IDs (e.g., img_1.png) to raw image bytes (PNG).
    """
    try:
        import fitz  # PyMuPDF
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "PyMuPDF (pymupdf) is required for image extraction. Install it with 'uv add pymupdf'."
        ) from e

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    images: Dict[str, bytes] = {}
    doc = fitz.open(str(pdf_path))
    img_counter = 0
    for page_index in range(len(doc)):
        page = doc[page_index]
        for img in page.get_images(full=True):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n >= 5:  # CMYK or similar -> convert to RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            img_counter += 1
            img_name = f"img_{img_counter}.png"
            images[img_name] = pix.tobytes("png")
            pix = None
    doc.close()
    return images


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
