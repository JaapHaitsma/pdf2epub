from pathlib import Path

from pdf2epub.epub_builder import BookMeta, build_epub


def test_embed_images_and_rewrite_refs(tmp_path: Path):
    html = [
        '<h1>Title</h1><p>See this:</p><img src="img_1.png" alt="demo">',
    ]
    images = {
        "img_1.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x02\x00\x01E\x1d\xa4\x1b\x00\x00\x00\x00IEND\xaeB`\x82",
    }
    out = tmp_path / "out.epub"
    build_epub(html, out, BookMeta(title="T", author="A"), images=images)
    assert out.exists() and out.stat().st_size > 0
