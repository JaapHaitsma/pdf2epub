Developer notes

- This project is set up for uv. Use `uv sync` to install.
- Optional dependency `pdfminer.six` is recommended for better text extraction.
- The CLI expects GEMINI_API_KEY in a .env or environment variable.
- To tweak chunk sizes, edit `pdf_reader.chunk_text`.
