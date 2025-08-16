from pathlib import Path

from pdf2epub.epub_builder import BookMeta, build_epub


def test_build_epub_tmp(tmp_path: Path):
    html = [
        "<h1>Title</h1><p>Hello world.</p>",
        "<h2>Chapter</h2><p>More text.</p>",
    ]
    out = tmp_path / "out.epub"
    build_epub(html, out, BookMeta(title="T", author="A"))
    assert out.exists() and out.stat().st_size > 0
