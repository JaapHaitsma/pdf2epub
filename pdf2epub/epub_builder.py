from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ebooklib import epub


@dataclass
class BookMeta:
    title: str
    author: str


def build_epub(html_sections: Iterable[str], output_path: Path, meta: BookMeta) -> None:
    book = epub.EpubBook()

    book.set_identifier("pdf2epub")
    book.set_title(meta.title)
    book.add_author(meta.author)

    spine = ["nav"]
    toc = []

    # Add each HTML section as a chapter
    for idx, html in enumerate(html_sections, start=1):
        chap = epub.EpubHtml(title=f"Chapter {idx}", file_name=f"chap_{idx}.xhtml", lang="en")
        chap.content = html
        book.add_item(chap)
        toc.append(chap)
        spine.append(chap)

    # Navigation
    book.toc = tuple(toc)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Basic CSS
    style = """
    body { font-family: serif; }
    h1, h2, h3 { font-family: sans-serif; }
    pre { background: #f4f4f4; padding: 0.5em; }
    """
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
    book.add_item(nav_css)

    book.spine = spine

    epub.write_epub(str(output_path), book)
