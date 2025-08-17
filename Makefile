# Input/Output configuration
# IN defaults to book.pdf; OUT is IN with .epub extension; SRC_DIR is IN's stem + _epub_src
IN ?= book.pdf


OUT ?= $(basename $(notdir $(IN))).epub
SRC_DIR ?= $(basename $(notdir $(IN)))_epub_src

.PHONY: setup test lint run


setup:
	uv sync

run:
	uv run pdf2epub $(IN) -o $(OUT) --keep-sources --debug --stream --cover-image cover.jpg

lint:
	uv run ruff check .

fix:
	uv run ruff check . --fix

test:
	uv run pytest -q

# Extra helpers
.PHONY: epub-from-src epubcheck open clean

epub-from-src:
	@rm -f $(OUT)
	@mkdir -p "$(SRC_DIR)"
	@if [ ! -f "$(SRC_DIR)/mimetype" ]; then printf "application/epub+zip" > "$(SRC_DIR)/mimetype"; fi
	@(cd "$(SRC_DIR)" && zip -X0 "../$(OUT)" mimetype && zip -Xr9D "../$(OUT)" META-INF OEBPS)
	@echo "Wrote $(OUT)"

epubcheck:
	@command -v epubcheck >/dev/null || { echo "Install epubcheck: (on Mac) brew install epubcheck"; exit 1; }
	@epubcheck "$(OUT)"

open:
	@open -a Books "$(OUT)"

clean:
	@rm -f "$(OUT)"
