from __future__ import annotations

from typing import Iterable, List, Dict, Any, Optional

import google.generativeai as genai


SYSTEM_PROMPT = (
    "You are a helpful editor. Clean and structure book-like text into chapters and sections. "
    "Preserve lists, code blocks, and headings. Output MUST be valid HTML5 fragment(s), "
    "using <h1> for title, <h2>/<h3> for sections, <p> for paragraphs, <ul>/<ol>/<li> for lists, "
    "and <pre><code> for code. Do not include images or <img> tags; summarize figure captions as plain text if needed."
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
        "OPF package (e.g., 'OEBPS/content.opf'), navigation (nav.xhtml) and chapters/sections as XHTML under OEBPS/. "
        "Use relative references and include a stylesheet (e.g., 'OEBPS/styles.css'). "
        "Do not include any binary images or <img> references; focus on text-only content."
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
        "OPF package (e.g., 'OEBPS/content.opf'), navigation (nav.xhtml) and chapters/sections as XHTML under OEBPS/. "
        "Use relative references and include a stylesheet (e.g., 'OEBPS/styles.css'). "
        "Do not include any binary images or <img> references; focus on text-only content."
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


def get_toc_from_pdf_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    console=None,
    debug_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if console:
        try:
            console.log("Requesting table of contents from Gemini (streaming)…")
        except Exception:
            pass
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Analyze this PDF and return a JSON object with a 'chapters' array. "
        "Each chapter item must be an object: {""index"": integer starting at 1, ""title"": string}. "
        "Do not include chapter content, only the list. Return only JSON."
    )
    stream = model.generate_content(
        [instruction, file],
        stream=True,
        generation_config={"response_mime_type": "application/json"},
    )
    collected: List[str] = []
    for chunk in stream:
        try:
            t = getattr(chunk, "text", None) or ""
        except Exception:
            t = ""
        if t:
            collected.append(t)
            if console:
                try:
                    console.out.write(t)
                    console.out.flush()
                except Exception:
                    try:
                        console.print(t)
                    except Exception:
                        pass
    if console:
        try:
            console.out.write("\n")
            console.out.flush()
        except Exception:
            console.print("")
    text = ("".join(collected)).strip()
    # Fallback if streaming returned no text (e.g., no Parts/blocked)
    if not text:
        if console:
            try:
                console.log("No streamed text; retrying TOC request without streaming…")
            except Exception:
                pass
        resp = model.generate_content(
            [instruction, file],
            generation_config={"response_mime_type": "application/json"},
        )
        text = _extract_text_from_response(resp).strip()
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_safely(text)
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise RuntimeError("Gemini did not return a valid TOC JSON with 'chapters'.")
    return data["chapters"]


def get_chapter_content_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    chapter_index: int,
    chapter_title: str,
    console=None,
    debug_path: Optional[str] = None,
) -> Dict[str, Any]:
    if console:
        try:
            console.log(f"Requesting chapter {chapter_index}: {chapter_title}")
        except Exception:
            pass
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    if console:
        try:
            console.log(f"Uploaded PDF file: {pdf_path}")
        except Exception:
            pass
    # Escape braces in title to avoid str.format interpreting them as placeholders
    safe_title = chapter_title.replace("{", "{{").replace("}", "}}")
    # Build with an f-string and escape literal JSON braces by doubling {{ }}
    instruction = (
        "Extract the specified chapter from the PDF and return JSON only. "
        "The JSON object must include: \n"
        "- \"xhtml\": a complete HTML5 fragment for this chapter (no scripts). \n"
        "Do not include images or <img> tags; summarize any figure captions in text. \n"
        f"Return only JSON. Chapter to extract: index={chapter_index}, title=\"{safe_title}\"."
    )
    if console:
        try:
            console.log(f"Sending instruction to Gemini: {instruction}")
        except Exception:
            pass
    stream = model.generate_content(
        [instruction, file],
        stream=True,
        generation_config={"response_mime_type": "application/json"},
    )
    if console:
        try:
            console.log("Streaming chapter content from Gemini…")
        except Exception:
            pass
    collected: List[str] = []
    for chunk in stream:
        try:
            t = getattr(chunk, "text", None) or ""
        except Exception:
            t = ""
        if t:
            collected.append(t)
            if console:
                try:
                    console.out.write(t)
                    console.out.flush()
                except Exception:
                    try:
                        console.print(t)
                    except Exception:
                        pass
    if console:
        try:
            console.out.write("\n")
            console.out.flush()
        except Exception:
            console.print("")
    text = ("".join(collected)).strip()
    # Fallback if streaming returned no text (e.g., no Parts/blocked)
    if not text:
        if console:
            try:
                console.log("No streamed text; retrying chapter request without streaming…")
            except Exception:
                pass
        resp = model.generate_content(
            [instruction, file],
            generation_config={"response_mime_type": "application/json"},
        )
        text = _extract_text_from_response(resp).strip()
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_safely(text)
    if not isinstance(data, dict):
        raise RuntimeError("Gemini did not return a valid chapter JSON object.")
    # Normalize keys
    xhtml = (
        data.get("xhtml")
        or data.get("html")
        or data.get("content")
        or data.get("chapter_html")
        or ""
    )
    return {"xhtml": xhtml}


def get_sections_from_pdf_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    console=None,
    debug_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Ask Gemini to enumerate all logical sections in reading order.

    Expected JSON: {"sections": [{"index": 1, "type": "chapter|foreword|toc|appendix|...", "title": "..."}, ...]}
    """
    if console:
        try:
            console.log("Requesting sections (front matter, chapters, back matter) from Gemini…")
        except Exception:
            pass
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Analyze this PDF and return JSON with all logical sections in reading order. "
        "Return only JSON with shape: {\"sections\": [{\"index\": integer starting at 1, \"type\": one of "
        "'title','copyright','dedication','preface','foreword','prologue','introduction','toc','chapter','appendix','acknowledgments','epilogue','afterword','notes','glossary','bibliography','index', \"title\": string}]}. "
        "Focus on logical structure, not pages. Do not include full content here."
    )
    stream = model.generate_content(
        [instruction, file],
        stream=True,
        generation_config={"response_mime_type": "application/json"},
    )
    collected: List[str] = []
    for chunk in stream:
        try:
            t = getattr(chunk, "text", None) or ""
        except Exception:
            t = ""
        if t:
            collected.append(t)
            if console:
                try:
                    console.out.write(t)
                    console.out.flush()
                except Exception:
                    try:
                        console.print(t)
                    except Exception:
                        pass
    if console:
        try:
            console.out.write("\n")
            console.out.flush()
        except Exception:
            console.print("")
    text = ("".join(collected)).strip()
    if not text:
        if console:
            try:
                console.log("No streamed text; retrying sections request without streaming…")
            except Exception:
                pass
        resp = model.generate_content(
            [instruction, file],
            generation_config={"response_mime_type": "application/json"},
        )
        text = _extract_text_from_response(resp).strip()
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_safely(text)
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise RuntimeError("Gemini did not return a valid sections JSON with 'sections'.")
    return data["sections"]


def get_section_content_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    section_index: int,
    section_type: str,
    section_title: str,
    console=None,
    debug_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Request a single section's content as XHTML + images JSON."""
    if console:
        try:
            console.log(f"Requesting section {section_index} [{section_type}]: {section_title}")
        except Exception:
            pass
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    if console:
        try:
            console.log(f"Uploaded PDF file: {pdf_path}")
        except Exception:
            pass
    safe_title = section_title.replace("{", "{{").replace("}", "}}")
    safe_type = (section_type or "section").strip()
    instruction = (
        "Extract the specified book section from the PDF and return JSON only. "
        "The JSON object must include: \n"
        "- \"xhtml\": a complete HTML5 fragment for this section (no scripts). \n"
        "Do not include images or <img> tags; summarize any figure captions in text. \n"
        f"Return only JSON. Section to extract: index={section_index}, type=\"{safe_type}\", title=\"{safe_title}\"."
    )
    stream = model.generate_content(
        [instruction, file],
        stream=True,
        generation_config={"response_mime_type": "application/json"},
    )
    if console:
        try:
            console.log("Streaming section content from Gemini…")
        except Exception:
            pass
    collected: List[str] = []
    for chunk in stream:
        try:
            t = getattr(chunk, "text", None) or ""
        except Exception:
            t = ""
        if t:
            collected.append(t)
            if console:
                try:
                    console.out.write(t)
                    console.out.flush()
                except Exception:
                    try:
                        console.print(t)
                    except Exception:
                        pass
    if console:
        try:
            console.out.write("\n")
            console.out.flush()
        except Exception:
            console.print("")
    text = ("".join(collected)).strip()
    if not text:
        if console:
            try:
                console.log("No streamed text; retrying section request without streaming…")
            except Exception:
                pass
        resp = model.generate_content(
            [instruction, file],
            generation_config={"response_mime_type": "application/json"},
        )
        text = _extract_text_from_response(resp).strip()
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_safely(text)
    if not isinstance(data, dict):
        raise RuntimeError("Gemini did not return a valid section JSON object.")
    xhtml = (
        data.get("xhtml")
        or data.get("html")
        or data.get("content")
        or data.get("section_html")
        or ""
    )
    return {"xhtml": xhtml}


def get_book_metadata_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    console=None,
    debug_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Ask Gemini to extract bibliographic metadata.

    Expected JSON fields (best effort):
    - title: string
    - authors: array of strings (or single author string)
    - isbn: string (digits and dashes), or null if unknown
    - language: ISO 639-1 like 'en' if determinable
    - publisher: string
    - date: ISO 8601 date (YYYY or YYYY-MM or YYYY-MM-DD)
    - description: string
    - subjects: array of strings
    """
    if console:
        try:
            console.log("Requesting book metadata from Gemini…")
        except Exception:
            pass
    file = genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Extract bibliographic metadata for this book and return JSON only. "
        "Fields: title (string); authors (array of strings); isbn (string, digits/dashes, null if none); "
        "language (ISO 639-1 like 'en' if known); publisher (string); date (YYYY or YYYY-MM or YYYY-MM-DD); "
        "description (string summary); subjects (array of strings)."
    )
    stream = model.generate_content(
        [instruction, file],
        stream=True,
        generation_config={"response_mime_type": "application/json"},
    )
    collected: List[str] = []
    for chunk in stream:
        try:
            t = getattr(chunk, "text", None) or ""
        except Exception:
            t = ""
        if t:
            collected.append(t)
            if console:
                try:
                    console.out.write(t)
                    console.out.flush()
                except Exception:
                    try:
                        console.print(t)
                    except Exception:
                        pass
    if console:
        try:
            console.out.write("\n")
            console.out.flush()
        except Exception:
            console.print("")
    text = ("".join(collected)).strip()
    if not text:
        if console:
            try:
                console.log("No streamed text; retrying metadata request without streaming…")
            except Exception:
                pass
        resp = model.generate_content(
            [instruction, file],
            generation_config={"response_mime_type": "application/json"},
        )
        text = _extract_text_from_response(resp).strip()
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_safely(text)
    if not isinstance(data, dict):
        return {}
    # Normalize
    title = data.get("title") or data.get("book_title") or ""
    authors = data.get("authors") or data.get("author") or []
    if isinstance(authors, str):
        authors = [authors]
    if not isinstance(authors, list):
        authors = []
    isbn = data.get("isbn") or data.get("identifier") or None
    language = data.get("language") or data.get("lang") or None
    publisher = data.get("publisher") or None
    date = data.get("date") or data.get("published") or data.get("publication_date") or None
    description = data.get("description") or data.get("summary") or None
    subjects = data.get("subjects") or data.get("keywords") or []
    if isinstance(subjects, str):
        subjects = [subjects]
    if not isinstance(subjects, list):
        subjects = []
    return {
        "title": title,
        "authors": authors,
        "isbn": isbn,
        "language": language,
        "publisher": publisher,
        "date": date,
        "description": description,
        "subjects": subjects,
    }


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


def _extract_text_from_response(resp) -> str:
    """Safely extract concatenated text from a non-stream response without raising
    when the library's `response.text` quick accessor fails due to missing Parts.

    Returns an empty string if nothing textual is available.
    """
    # First try the convenience property; it may raise if no parts exist
    try:
        t = getattr(resp, "text")
        if isinstance(t, str):
            return t
    except Exception:
        pass
    # Fall back to iterating candidates/parts
    texts: list[str] = []
    try:
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                for part in parts:
                    t = getattr(part, "text", None)
                    if isinstance(t, str) and t:
                        texts.append(t)
    except Exception:
        return ""
    return "".join(texts)
