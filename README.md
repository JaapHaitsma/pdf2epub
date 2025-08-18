# pdf2epub

Convert a PDF to an EPUB using Google Gemini (2.5 Pro) with a simple, resilient CLI.
This project has been vibe coded with Github copilot with GPT-5 (previe)

## Highlights

-   Conversion is done Section by Section (TOC, Introduction, Chapter 1, Chapter 2, etc).
-   Titles and numbering (e.g., “Chapter 3”, “1.2.4”) are preserved in XHTML and TOC.
-   Image detection via Gemini + cropping via PyMuPDF; embeds images under `OEBPS/images/` and rewrites `<img>` refs.
-   Skips decorative boxes/lines around text using geometry heuristics (no borders/highlights/separators).
-   Optional cover image support:
    -   Auto: first PDF page rendered as cover.
    -   Manual: pass `--cover-image path/to/image.(jpg|png)`.
-   Clean XHTML with soft line-wrap at ≤150 chars; EPUB2-compatible package with NCX and nav.
-   Debug JSON artifacts only when `--debug` is passed.

## Requirements

-   Python 3.13+
-   `uv` (https://docs.astral.sh/uv/)
-   Google Gemini API key

## Setup

1. Create `.env` in the project root:
    ```env
    GEMINI_API_KEY=your_api_key_here
    # optional
    GEMINI_MODEL=gemini-2.5-pro
    ```
2. Install and check the CLI:
    ```sh
    uv sync
    uv run pdf2epub --help
    ```

## Usage

Basic:

```sh
uv run pdf2epub input.pdf -o output.epub
```

Options:

-   `--keep-sources` Keep the unpacked EPUB source folder next to the output.
-   `--debug` Write raw Gemini JSON responses (sections, section content, metadata) next to the output.
-   `--cover-image PATH` Use a specific image file as the book cover (overrides auto-cover).
-   `--no-auto-cover` Disable using the first PDF page as a cover when no explicit cover image is provided.

Examples:

```sh
# With explicit cover image
uv run pdf2epub book.pdf -o book.epub --cover-image cover.jpg

# Keep sources for inspection and write debug JSON
uv run pdf2epub book.pdf -o book.epub --keep-sources --debug
```

## Makefile shortcuts

The Makefile provides convenient targets and variables to speed up common tasks.

-   Variables (override on the command line):

    -   IN: input PDF path. Default: `book.pdf`
    -   OUT: output EPUB filename. Default: `$(basename $(notdir $(IN))).epub`
    -   SRC_DIR: source folder name for unpacked EPUB. Default: `$(basename $(notdir $(IN)))_epub_src`

-   Targets:
    -   setup: Install dependencies via `uv sync`.
    -   run: Run the converter with useful flags:
        -   `pdf2epub $(IN) -o $(OUT) --keep-sources --debug --stream --cover-image cover.jpg`
        -   Note: expects a `cover.jpg` in the project root; change or remove this flag in the Makefile if not desired.
    -   lint: Lint with Ruff.
    -   fix: Lint and auto-fix with Ruff.
    -   test: Run the test suite with pytest.
    -   epub-from-src: Zip an EPUB from `$(SRC_DIR)` (useful after manual edits to unpacked EPUB files).
    -   epubcheck: Validate `$(OUT)` with epubcheck (requires epubcheck installed; on macOS: `brew install epubcheck`).
    -   open: Open `$(OUT)` in Apple Books (macOS).
    -   clean: Remove `$(OUT)`.

Examples:

```sh
# Install deps
make setup

# Convert with defaults (book.pdf -> book.epub)
make run

# Convert a specific file and choose output name
make run IN=manuscript.pdf OUT=mybook.epub

# Validate and open the result
make epubcheck
make open

# Rebuild an EPUB from an existing unpacked source directory
make epub-from-src SRC_DIR=mybook_epub_src OUT=mybook.epub
```

## How it works

-   Gemini is prompted to enumerate sections and return per-section XHTML + image boxes. Strict JSON parsing with streaming fallback and retries.
-   Images: For each section, Gemini may return normalized `box_2d` + `page_index`. PyMuPDF crops those regions from the PDF and writes `OEBPS/images/*` files; decorative rectangles/lines are filtered out.
-   Packaging: We assemble `OEBPS/*.xhtml`, `content.opf`, `toc.ncx`, `nav.xhtml`, `styles.css`, and zip into EPUB with correct mimetype placement. EPUB 2.0-compatible NCX is included for broader reader support.
-   Cover: If provided, the cover image and `cover.xhtml` are added and placed first in the spine, with EPUB2 cover metadata and a guide entry. If not provided, the first PDF page is rendered as a JPEG cover by default (unless `--no-auto-cover`).

## Notes

-   Network hiccups: the Gemini calls use streaming with fallback to non-streaming and exponential backoff for transient errors.
-   XHTML is soft-wrapped to ≤150 characters per line to keep diffs and editors friendly; `<pre>/<code>` blocks aren’t wrapped.
-   Only “by section” mode is implemented; other modes were removed for reliability.

## Development & Testing

```sh
uv run pytest -q
```

Linting (ruff is configured in `pyproject.toml`):

```sh
uv run ruff check .
```

There’s also a small `epub_builder.py` using EbookLib for isolated tests, but the main pipeline uses a custom packager for compatibility and speed.

## License

MIT
