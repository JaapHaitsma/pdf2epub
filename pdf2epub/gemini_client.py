from __future__ import annotations

from typing import Iterable, List, Dict, Any, Optional

import google.generativeai as genai


SYSTEM_PROMPT = (
    "You are a helpful editor. Clean and structure book-like text into chapters and sections. "
    "Preserve lists, code blocks, and references to images. Output MUST be valid HTML5 fragment(s), "
    "using <h1> for title, <h2>/<h3> for sections, <p> for paragraphs, <ul>/<ol>/<li> for lists, "
    "and <pre><code> for code. If the text indicates an image (e.g., 'Figure X: ...' or placeholder), "
    "emit <img src=\"img_PLACEHOLDER\" alt=\"...\"> where src matches provided image names when possible."
)


def init_client(api_key: str, model: str) -> genai.GenerativeModel:
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name=model, system_instruction=SYSTEM_PROMPT)


def generate_structured_html(model: genai.GenerativeModel, chunks: Iterable[str]) -> list[str]:
    html_parts: list[str] = []
    for chunk in chunks:
        prompt = (
            "Convert the following PDF-extracted text into clean, structured HTML suitable for EPUB.\n\n"
            f"TEXT:\n{chunk}\n\n"
            "Rules: produce only HTML, no markdown, no frontmatter, no explanations. If the text references "
            "figures or images, include <img> tags with src pointing to 'img_#.png' where applicable."
        )
        resp = model.generate_content(prompt)
        html = resp.text or ""
        html_parts.append(html.strip())
    return html_parts


def ocr_pages_to_html(model: genai.GenerativeModel, page_pngs: List[bytes]) -> list[str]:
    """Send page images to Gemini and receive structured HTML.

    Strategy: send pages in small batches; for each batch, instruct the model to produce
    clean HTML chapters/sections preserving reading order and including <img> tag placeholders
    where appropriate.
    """
    html_parts: list[str] = []
    # Process pages in batches of e.g., 5 to balance token/image limits
    batch_size = 5
    for i in range(0, len(page_pngs), batch_size):
        imgs = page_pngs[i : i + batch_size]
        # Prepare prompt and parts: first the instruction, then the images
        prompt = (
            "Perform OCR on the following scanned book pages and return clean, structured HTML. "
            "Use headings (<h1>, <h2>, <h3>), paragraphs (<p>), lists, and code blocks as needed. "
            "Include <img src=\"img_#.png\"> placeholders when figures/illustrations are present in the images. "
            "Return only HTML with no explanations."
        )
        parts = [prompt]
        for img in imgs:
            parts.append({"mime_type": "image/png", "data": img})
        resp = model.generate_content(parts)
        html = (resp.text or "").strip()
        if html:
            html_parts.append(html)
    return html_parts


def upload_pdf_and_request_epub_manifest(model: genai.GenerativeModel, pdf_path: str) -> Dict[str, Any]:
    """Upload a PDF to Gemini and request a structured EPUB manifest (files list).

    Expected response: JSON with shape {"files": [{"path": str, "content": str, "encoding": "utf-8"}],
    optionally {"images": ["images/img_1.png", ...]} and metadata. We will post-process to ensure the
    'mimetype' file and minimal required structure exist.
    """
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Analyze this PDF and produce a complete EPUB file set as a JSON manifest. "
        "Return only JSON (no markdown). The manifest must be an object with key 'files' as an array of objects. "
        "Each file object must have: 'path' (string path under EPUB root), 'content' (string), and optionally 'encoding' ('utf-8'). "
        "Include standard EPUB structure: 'mimetype' at root with value 'application/epub+zip', 'META-INF/container.xml', "
        "OPF package (e.g., 'OEBPS/content.opf'), navigation (nav.xhtml) and chapters as XHTML under OEBPS/. "
        "Use relative references and include a stylesheet (e.g., 'OEBPS/styles.css'). "
        "If the book contains figures, reference them via <img src=\"images/img_#.png\"> and include an 'images' array listing those paths."
    )
    resp = model.generate_content(
        [instruction, file],
        generation_config={"response_mime_type": "application/json"},
    )
    text = (resp.text or "").strip()
    data = _parse_json_safely(text)
    if not isinstance(data, dict) or "files" not in data:
        raise RuntimeError("Gemini did not return a valid EPUB manifest JSON.")
    return data


def upload_pdf_and_request_epub_manifest_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    console=None,
    debug_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Same as upload_pdf_and_request_epub_manifest, but streams Gemini output to the console.

    This provides live feedback in the terminal so you can see progress as the model generates the JSON.
    """
    if console:
        try:
            console.log("Uploading PDF to Gemini…")
        except Exception:
            pass
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Analyze this PDF and produce a complete EPUB file set as a JSON manifest. "
        "Return only JSON (no markdown). The manifest must be an object with key 'files' as an array of objects. "
        "Each file object must have: 'path' (string path under EPUB root), 'content' (string), and optionally 'encoding' ('utf-8'). "
        "Include standard EPUB structure: 'mimetype' at root with value 'application/epub+zip', 'META-INF/container.xml', "
        "OPF package (e.g., 'OEBPS/content.opf'), navigation (nav.xhtml) and chapters as XHTML under OEBPS/. "
        "Use relative references and include a stylesheet (e.g., 'OEBPS/styles.css'). "
        "If the book contains figures, reference them via <img src=\"images/img_#.png\"> and include an 'images' array listing those paths."
    )
    if console:
        try:
            console.log("Requesting EPUB manifest from Gemini (streaming)…")
        except Exception:
            pass
    stream = model.generate_content(
        [instruction, file],
        stream=True,
        generation_config={"response_mime_type": "application/json"},
    )
    collected = []
    for chunk in stream:
        text = chunk.text or ""
        if text:
            collected.append(text)
            if console:
                try:
                    # Write without forcing a newline for a more natural stream
                    console.out.write(text)
                    console.out.flush()
                except Exception:
                    console.print(text)
    # Add a newline after streaming
    if console:
        try:
            console.out.write("\n")
            console.out.flush()
        except Exception:
            console.print("")
    text = ("".join(collected)).strip()
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_safely(text)
    if not isinstance(data, dict) or "files" not in data:
        raise RuntimeError("Gemini did not return a valid EPUB manifest JSON.")
    return data


def _parse_json_safely(text: str) -> Any:
    import json
    # Strip markdown fences if present (``` or ```json)
    if text.lstrip().startswith("```"):
        s = text.lstrip()
        first_nl = s.find("\n")
        if first_nl != -1:
            closing = s.rfind("```")
            if closing != -1 and closing > first_nl:
                text = s[first_nl + 1 : closing]
    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        # Fallback: extract the largest plausible JSON object by brace matching
        start = text.find("{")
        if start != -1:
            depth = 0
            end_index = -1
            for i, ch in enumerate(text[start:], start=start):
                if ch == "{" or ch == "[":
                    depth += 1
                elif ch == "}" or ch == "]":
                    depth -= 1
                    if depth == 0:
                        end_index = i
            if end_index != -1:
                snippet = text[start : end_index + 1]
                return json.loads(snippet)
        raise
