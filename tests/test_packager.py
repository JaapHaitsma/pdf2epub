from pathlib import Path

from pdf2epub.packager import write_manifest_to_dir, zip_epub_from_dir


def test_packager_builds_valid_epub(tmp_path: Path):
    manifest = {
        "files": [
            {"path": "mimetype", "content": "application/epub+zip", "encoding": "utf-8"},
            {
                "path": "META-INF/container.xml",
                "content": """
<?xml version='1.0' encoding='utf-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
  <rootfiles>
    <rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>
  </rootfiles>
</container>
                """.strip(),
            },
            {"path": "OEBPS/content.opf", "content": "<package></package>"},
            {"path": "OEBPS/nav.xhtml", "content": "<nav></nav>"},
            {"path": "OEBPS/ch1.xhtml", "content": "<h1>Hi</h1><p>Text</p>"},
        ]
    }
    src_dir = tmp_path / "src"
    out_file = tmp_path / "out.epub"
    write_manifest_to_dir(manifest, src_dir)
    zip_epub_from_dir(src_dir, out_file)
    assert out_file.exists() and out_file.stat().st_size > 0
