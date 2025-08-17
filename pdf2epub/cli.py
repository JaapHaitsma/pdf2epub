from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.traceback import install

from .pipeline import convert_pdf_to_epub

install(show_locals=False)
console = Console()


@dataclass
class Args:
    input_pdf: Path
    output_epub: Path
    keep_sources: bool
    by_section: bool


def parse_args(argv: list[str]) -> Args:
    import argparse

    parser = argparse.ArgumentParser(
        prog="pdf2epub",
        description="Upload a PDF to Google Gemini to generate a complete EPUB",
    )
    parser.add_argument("input_pdf", type=Path, help="Path to input PDF file")
    parser.add_argument(
        "-o",
        "--output",
        dest="output_epub",
        type=Path,
    help="Path to output .epub (default: input name with .epub)",
    )
    parser.add_argument(
        "--keep-sources",
        action="store_true",
        help="Keep the unpacked EPUB files on disk",
    )
    parser.add_argument(
        "--by-section",
        action="store_true",
        default=True,
        help="Extract the book section-by-section (default and only mode)",
    )

    ns = parser.parse_args(argv)
    input_pdf: Path = ns.input_pdf
    if not ns.output_epub:
        output_epub = input_pdf.with_suffix(".epub")
    else:
        output_epub = ns.output_epub

    return Args(
        input_pdf=input_pdf,
        output_epub=output_epub,
        keep_sources=ns.keep_sources,
        by_section=True,  # only supported mode
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        console.print("[red]Error:[/] GEMINI_API_KEY not set. Add it to .env or your environment.")
        return 2

    model = os.getenv("GEMINI_MODEL") or "gemini-2.5-pro"

    try:
        convert_pdf_to_epub(
            input_pdf=args.input_pdf,
            output_epub=args.output_epub,
            api_key=api_key,
            model=model,
            keep_sources=args.keep_sources,
            console=console,
            by_section=True,
        )
    except FileNotFoundError as e:
        console.print(f"[red]File not found:[/] {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Failed:[/] {e}")
        return 1

    console.print(f"[green]Done:[/] Wrote {args.output_epub}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
