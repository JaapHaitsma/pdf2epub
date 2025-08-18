from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

from ebooklib import epub


@dataclass
class BookMeta:
    title: str
    author: str


def build_epub(
    html_sections: Iterable[str],
    output_path: Path,
    meta: BookMeta,
    images: Dict[str, bytes] | None = None,
) -> None:
    book = epub.EpubBook()

    book.set_identifier("pdf2epub")
    book.set_title(meta.title)
    book.add_author(meta.author)

    spine = ["nav"]
    toc = []

    # Add images first so chapters can link to them
    img_id_map: dict[str, str] = {}
    if images:
        for name, data in images.items():
            item = epub.EpubItem(
                uid=name,
                file_name=f"images/{name}",
                media_type="image/png",
                content=data,
            )
            book.add_item(item)
            img_id_map[name] = item.file_name

    # Add each HTML section as a chapter and rewrite image refs to packaged paths
    for idx, html in enumerate(html_sections, start=1):
        chap = epub.EpubHtml(title=f"Chapter {idx}", file_name=f"chap_{idx}.xhtml", lang="en")
        if img_id_map:
            tmp = html
            for original, file_name in img_id_map.items():
                tmp = tmp.replace(f'src="{original}"', f'src="{file_name}"')
                tmp = tmp.replace(f"src='{original}'", f"src='{file_name}'")
            chap.content = tmp
        else:
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
