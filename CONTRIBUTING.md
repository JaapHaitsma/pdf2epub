# Contributing

Thanks for contributing to pdf2epub!

Setup
- Use `uv` for dependency management: `uv sync`.
- Create a `.env` with `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`).

Workflow
- Branch from the latest default branch.
- Keep changes focused and incremental; prefer small PRs.
- Update README/README_DEV for user-facing or workflow changes.

Quality
- Tests: run `uv run pytest -q` before submitting.
- Lint: `uv run ruff check .` (line length configured in `pyproject.toml`).
- Avoid breaking public CLI behavior. Add tests for new flags or flows.

Coding
- Keep functions small and testable; prefer pure transformations.
- Avoid unnecessary dependencies; pin widely used libraries in `pyproject.toml`.
- Maintain compatibility of EPUB outputs (OPF 2 + NCX + nav).

PR Checklist
- [ ] Tests updated/added
- [ ] Docs updated (README/README_DEV)
- [ ] Lint clean
