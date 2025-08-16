from pathlib import Path

import builtins

from pdf2epub import pipeline


def test_pipeline_builds_epub_with_mocks(tmp_path: Path, monkeypatch):
    # Prepare fake input and output paths
    in_pdf = tmp_path / "input.pdf"
    in_pdf.write_bytes(b"%PDF-1.4\n% minimal placeholder\n")
    out_epub = tmp_path / "output.epub"

    # Mock PDF extraction to avoid real parsing
    monkeypatch.setattr(pipeline, "extract_text_by_pages", lambda p: [
        "Sample page 1 text.",
        "Sample page 2 text.",
    ])

    # Mock Gemini client init and content generation
    class DummyModel:
        pass

    monkeypatch.setattr(pipeline, "init_client", lambda api_key, model: DummyModel())
    monkeypatch.setattr(
        pipeline,
        "generate_structured_html",
        lambda _model, chunks: [
            "<h1>Title</h1><p>From chunk 1</p>",
            "<h2>Section</h2><p>From chunk 2</p>",
        ],
    )

    # Run conversion
    pipeline.convert_pdf_to_epub(
        input_pdf=in_pdf,
        output_epub=out_epub,
        api_key="dummy",
        model="gemini-2.5-pro",
        title="Mocked Title",
        author="Mocked Author",
        console=None,
    )

    assert out_epub.exists() and out_epub.stat().st_size > 0
