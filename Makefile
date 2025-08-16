.PHONY: setup test lint run

setup:
	uv sync

run:
	uv run pdf2epub --help

lint:
	uv run ruff check .

fix:
	uv run ruff check . --fix

test:
	uv run pytest -q
