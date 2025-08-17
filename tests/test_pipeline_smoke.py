from pathlib import Path

from pdf2epub import pipeline


def test_pipeline_builds_epub_with_mocks(tmp_path: Path, monkeypatch):
    # Prepare fake input and output paths
    in_pdf = tmp_path / "input.pdf"
    in_pdf.write_bytes(b"%PDF-1.4\n% minimal placeholder\n")
    out_epub = tmp_path / "output.epub"

    # Mock Gemini client init and section-by-section generation
    class DummyModel:
        pass

    monkeypatch.setattr(pipeline, "init_client", lambda api_key, model: DummyModel())
    # Sections listing
    monkeypatch.setattr(
        pipeline,
        "get_sections_from_pdf_verbose",
        lambda _model, _path, **_kw: [
            {"index": 1, "type": "title", "title": "Title Page"},
            {"index": 2, "type": "chapter", "title": "Chapter One"},
        ],
    )
    # Section content
    def _sec_content(_model, _path, *, section_index, section_type, section_title, **_kw):
        if section_index == 1:
            return {"xhtml": "<h1>Title Page</h1>"}
        return {"xhtml": "<h1>Chapter One</h1>"}
    monkeypatch.setattr(pipeline, "get_section_content_verbose", _sec_content)
    # Metadata
    monkeypatch.setattr(
        pipeline,
        "get_book_metadata_verbose",
        lambda _model, _path, **_kw: {"title": "Sample Book", "authors": ["A"], "language": "en"},
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
