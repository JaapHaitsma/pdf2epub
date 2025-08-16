# pdf2epub (uv project)

Convert a PDF to an EPUB using Google Gemini (2.5 Pro) with a simple CLI.

## Features

- Reads a PDF and sends text content to Google Gemini for structured chapterization and cleanup.
- Builds a valid EPUB using EbookLib.
- Config via `.env` file (`GEMINI_API_KEY`).
- Fast packaging and running with `uv`.

## Requirements

- Python 3.9+
- `uv` (https://docs.astral.sh/uv/)
- A Google Gemini API key (Model: `gemini-2.5-pro` or latest available).

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
uv run pdf2epub path/to/input.pdf -o output.epub --title "Optional Title" --author "You"
```

## How it works

- Extracts text using `pdfminer.six` (already included).
- Sends chunks to Gemini asking for cleaned/structured content (chapters, headings, paragraphs, lists, code blocks if detected).
- Builds an EPUB from the model's structured output.

## Notes

- If you need images in the EPUB, extend `epub_builder.py` to embed images and references.
- The Gemini API may have token limits; this CLI chunks content and merges responses.

## Testing

```sh
uv run pytest -q
```

## License

MIT
