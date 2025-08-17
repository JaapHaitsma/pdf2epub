Developer notes

- Project uses `uv` for fast, isolated workflows. Use `uv sync` to install.
- The CLI expects `GEMINI_API_KEY` in `.env` or environment; optional `GEMINI_MODEL`.

Architecture
- `pdf2epub/cli.py`: argument parsing, .env, wires to pipeline.
- `pdf2epub/pipeline.py`: orchestrates conversion (by-section only), EPUB assembly, packaging.
- `pdf2epub/gemini_client.py`: Gemini calls with robust JSON-only prompts, streaming fallback, and retries.
- `pdf2epub/packager.py`: writes files to a temp dir and zips EPUB (mimetype stored first).
- `pdf2epub/epub_builder.py`: small helper for tests using EbookLib.

Key behaviors
- Section titles come from Gemini and are used (slugified) for XHTML filenames. Duplicate-safe suffixes are appended.
- Numbering is preserved in both XHTML headings and TOC. `_ensure_title_heading` injects an `<h1>` if missing.
- Images: Gemini returns `{filename,label,box_2d,page_index}`; PyMuPDF crops and writes `OEBPS/images/*`. Decorative rectangles/lines (thin borders, separators) are filtered out.
- Cover: If `--cover-image` is provided, it’s used; otherwise the first PDF page is rendered as a JPEG cover unless `--no-auto-cover` is set. OPF includes cover meta, cover.xhtml, guide, and puts cover first in spine.
- XHTML is soft-wrapped to ≤150 chars (excludes `<pre>/<code>`).
- Debug JSON dumps are only written when `--debug` is passed.

Prompts
- System prompt: preserve headings, lists, code; keep numbering prefixes/labels.
- Sections listing: return logical sections with titles preserving numbering.
- Section content: return JSON `{xhtml, images[]}`, keep numbering unchanged, include only meaningful figures (no decorative elements), normalized `box_2d`.

Running
```sh
uv run pdf2epub --by-section input.pdf -o output.epub [--keep-sources] [--debug] [--cover-image path] [--no-auto-cover]
```

Testing
```sh
uv run pytest -q
```

Linting
```sh
uv run ruff check .
```
