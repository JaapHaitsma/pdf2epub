from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from .epub_builder import BookMeta, build_epub
from .gemini_client import generate_structured_html, init_client
from .pdf_reader import chunk_text, extract_text_by_pages


def convert_pdf_to_epub(
    input_pdf: Path,
    output_epub: Path,
    api_key: str,
    model: str,
    title: Optional[str] = None,
    author: Optional[str] = None,
    console: Optional[Console] = None,
) -> None:
    console = console or Console()

    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)

    console.log(f"Reading PDF: {input_pdf}")
    pages = extract_text_by_pages(input_pdf)
    if not pages:
        raise RuntimeError("No text extracted from PDF.")

    console.log(f"Extracted ~{sum(len(p) for p in pages):,} chars across {len(pages)} page chunks")

    chunks = chunk_text(pages)
    console.log(f"Created {len(chunks)} chunk(s) for model processing")

    client = init_client(api_key=api_key, model=model)

    console.log("Calling Gemini to structure content…")
    html_sections = generate_structured_html(client, chunks)

    meta = BookMeta(
        title=title or input_pdf.stem,
        author=author or "Unknown",
    )

    console.log(f"Building EPUB: {output_epub}")
    build_epub(html_sections, output_epub, meta)
