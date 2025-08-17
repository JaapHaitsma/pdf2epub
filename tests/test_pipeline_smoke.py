from pathlib import Path

from pdf2epub import pipeline


def test_pipeline_builds_epub_with_mocks(tmp_path: Path, monkeypatch):
    # Prepare fake input and output paths
    in_pdf = tmp_path / "input.pdf"
    in_pdf.write_bytes(b"%PDF-1.4\n% minimal placeholder\n")
    out_epub = tmp_path / "output.epub"

    # Mock Gemini client init and manifest generation (new path)
    class DummyModel:
        pass

    monkeypatch.setattr(pipeline, "init_client", lambda api_key, model: DummyModel())
    monkeypatch.setattr(
        pipeline,
        "upload_pdf_and_request_epub_manifest_verbose",
        lambda _model, _path: {
            "files": [
                {"path": "mimetype", "content": "application/epub+zip"},
                {"path": "META-INF/container.xml", "content": "<container/>"},
                {"path": "OEBPS/content.opf", "content": "<package/>"},
                {"path": "OEBPS/ch1.xhtml", "content": "<h1>Ch1</h1>"},
            ]
        },
    )

    # Run conversion
    pipeline.convert_pdf_to_epub(
        input_pdf=in_pdf,
        output_epub=out_epub,
        api_key="dummy",
        model="gemini-2.5-pro",
        console=None,
    )

    assert out_epub.exists() and out_epub.stat().st_size > 0
