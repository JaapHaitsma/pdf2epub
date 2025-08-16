# pdf2epub (uv project)

Convert a PDF to an EPUB using Google Gemini (2.5 Pro) with a simple CLI.

## Features

-   Reads a PDF and sends text content to Google Gemini for structured chapterization and cleanup.
-   Extracts and embeds images into the EPUB (via PyMuPDF) and rewrites <img> src references.
-   Builds a valid EPUB using EbookLib.
-   Config via `.env` file (`GEMINI_API_KEY`).
-   Fast packaging and running with `uv`.

## Requirements

-   Python 3.13+
-   `uv` (https://docs.astral.sh/uv/)
-   A Google Gemini API key (Model: `gemini-2.5-pro` or latest available).

## Setup

1. Create `.env` in the project root:
    ```env
    GEMINI_API_KEY=your_api_key_here
    GEMINI_MODEL=gemini-2.5-pro
    ```
2. Install dependencies and run via `uv`:
    ```sh
    uv sync
    uv run pdf2epub --help
    ```

## Usage

```sh
uv run pdf2epub path/to/input.pdf -o output.epub
```

This CLI uploads the PDF to Gemini and requests a full EPUB manifest, writes the files, and zips them.

````

## How it works

-   Extracts text using `pdfminer.six` (already included).
-   Extracts images using PyMuPDF (included) and packages them under `images/` in the EPUB.
-   Sends chunks to Gemini asking for cleaned/structured content; prompt encourages <img src="img_#.png"> tags where applicable, which are rewritten to packaged image paths.
-   Builds an EPUB from the model's structured output.

## Notes

-   Image extraction uses PyMuPDF; unusual color spaces are converted to RGB PNG.
-   The Gemini API may have token limits; this CLI chunks content and merges responses.

## Testing

```sh
uv run pytest -q
````

## License

MIT
