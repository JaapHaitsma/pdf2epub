from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from .gemini_client import init_client, upload_pdf_and_request_epub_manifest
from .packager import write_manifest_to_dir, zip_epub_from_dir


def convert_pdf_to_epub(
    input_pdf: Path,
    output_epub: Path,
    api_key: str,
    model: str,
    keep_sources: bool = False,
    console: Optional[Console] = None,
) -> None:
    console = console or Console()

    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)

    console.log(f"Reading PDF: {input_pdf}")
    client = init_client(api_key=api_key, model=model)

    console.log("Delegating EPUB generation to Gemini (manifest mode)…")
    manifest = upload_pdf_and_request_epub_manifest(client, str(input_pdf))
    temp_dir = output_epub.parent / (output_epub.stem + "_epub_src")
    if temp_dir.exists():
        # Best-effort clean
        import shutil
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    write_manifest_to_dir(manifest, temp_dir)
    console.log("Zipping EPUB…")
    zip_epub_from_dir(temp_dir, output_epub)
    if not keep_sources:
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except Exception:
            pass
    return
