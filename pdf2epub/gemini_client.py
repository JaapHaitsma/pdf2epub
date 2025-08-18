import time
import json
import re
from typing import Any, Dict, Iterable, List, Optional

import google.generativeai as genai

SYSTEM_PROMPT = (
    "You are a precise, conservative editor. Clean and structure book-like text into chapters and sections. "
    "Preserve lists, code blocks, and headings. Output MUST be well-formed XHTML 1.1 fragment(s) suitable for EPUB 2: "
    "use <h1> for the title, <h2>/<h3> for sections, <p> for paragraphs, and <ul><li> for lists (never use <ol>; include any original numbering like '1.' '(a)' inside the <li> text). "
    "Use <pre><code> for code. Always preserve numbering prefixes and labels exactly as in the document (e.g., 'Chapter 3', '1.2.4', '§ 2.3'). "
    "When a prompt explicitly requests images, include them as instructed; otherwise do not add images. "
    "Well-formedness rules: close all tags; never concatenate an end tag and the next start tag without a closing angle bracket (use </p><p>, never </p<p>); "
    "use self-closing empty elements where appropriate (e.g., <br />). Use straight ASCII quotes (\" and ') in text; do NOT use typographic quotes or HTML entities for quotes (no &ldquo;, &rdquo;, &lsquo;, &rsquo;). "
    "Do NOT use HTML named entities like &copy;, &nbsp;, &mdash;. Prefer literal Unicode symbols (e.g., ©, —) or numeric character references (e.g., &#169;). The only named entities permitted are the five XML predefined ones: &amp;, &lt;, &gt;, &quot;, &apos;. "
    "Escape reserved characters correctly: in text nodes write & as &amp;, < as &lt;, and > as &gt;; for attribute values use double quotes and escape embedded quotes as &quot;. Never output a raw & character. "
    "When embedding XHTML in a JSON string value, you MUST output strict RFC 8259 JSON: escape embedded double quotes as \\\" and backslashes as \\\\, represent newlines as \\n, do not emit control characters, comments, code fences, or trailing commas. Return a single top-level JSON object only."
)


def init_client(api_key: str, model: str, **kwargs):
    genai.configure(api_key=api_key)
    generation_config = {
        "temperature": 0.0,
        "top_p": 0.3,
        "top_k": 1,
        "candidate_count": 1,
        "response_mime_type": "application/json",
    }
    generation_config.update(kwargs or {})
    return genai.GenerativeModel(model_name=model, system_instruction=SYSTEM_PROMPT)


def _gen_config_json() -> Dict[str, Any]:
    """Low randomness + JSON response."""
    return {
        "temperature": 0.0,
        "top_p": 0.3,
        "top_k": 1,
        "candidate_count": 1,
        "response_mime_type": "application/json",
    }


def _gen_config_text() -> Dict[str, Any]:
    """Low randomness for plain text/HTML responses."""
    return {
        "temperature": 0.0,
        "top_p": 0.3,
        "top_k": 1,
        "candidate_count": 1,
    }


def upload_pdf_once(pdf_path: str, *, console=None) -> Any:
    """Upload the PDF once and return the uploaded file handle.

    The returned object can be reused in multiple model.generate_content calls
    within this process, avoiding repeated uploads.
    """
    if console:
        try:
            console.log(f"Uploading PDF once: {pdf_path}")
        except Exception:
            pass
    return genai.upload_file(pdf_path, mime_type="application/pdf")


def generate_structured_html(model: genai.GenerativeModel, chunks: Iterable[str]) -> list[str]:
    html_parts: list[str] = []
    for chunk in chunks:
        prompt = (
            "Convert the following PDF-extracted text into clean, structured HTML suitable for EPUB.\n\n"
            f"TEXT:\n{chunk}\n\n"
            "Rules: produce only HTML, no markdown, no frontmatter, no explanations. Use <ul><li> for lists; do NOT use <ol>. Preserve the original numbering/prefix characters by writing them inside each <li> (e.g., '1. First', '(a) Item'). Use straight ASCII quotes (\" and ') in text, avoid &ldquo; &rdquo; &lsquo; &rsquo;. Do NOT use HTML named entities like &copy;, &nbsp;, &mdash;; prefer Unicode symbols (©, —) or numeric entities (e.g., &#169;). Only &amp;, &lt;, &gt;, &quot;, &apos; are allowed as named entities. Properly escape reserved characters: & -> &amp;, < -> &lt;, > -> &gt; in text; in attributes use double quotes and escape embedded quotes as &quot;. Never leave a raw & in output."
        )
        resp = model.generate_content(prompt)
        html = resp.text or ""
        html_parts.append(html.strip())
    return html_parts


def get_sections_from_pdf_verbose(
    model: genai.GenerativeModel,
    pdf_path: Optional[str] = None,
    *,
    uploaded_file: Any | None = None,
    console=None,
    debug_path: Optional[str] = None,
    stream_console: bool = False,
) -> List[Dict[str, Any]]:
    """Ask Gemini to enumerate all logical sections in reading order."""
    if console:
        try:
            console.log("Requesting sections (front matter, chapters, back matter) from Gemini…")
        except Exception:
            pass
    file = uploaded_file or (
        genai.upload_file(pdf_path, mime_type="application/pdf") if pdf_path else None
    )
    if file is None:
        raise ValueError("No uploaded_file provided and no pdf_path to upload from.")
    instruction = (
        "Analyze this PDF and return JSON with all logical sections in reading order. "
        "Return only JSON with shape: {\"sections\": [{\"index\": integer starting at 1, \"type\": one of "
        "'title','copyright','dedication','preface','foreword','prologue','introduction','toc','chapter','appendix','acknowledgments','epilogue','afterword','notes','glossary','bibliography','index', \"title\": string}]}. "
        "Important: the \"title\" must preserve any numbering and labels exactly as in the document (e.g., 'Chapter 3', '1.1 Overview', '§ 2.3'). "
        "Do not invent, drop, or renumber headings. Focus on logical structure, not pages. Do not include full content here. "
        "Output MUST be strict RFC 8259 JSON: double-quote all keys/strings, no trailing commas, no comments/backticks/fences."
    )
    text = ""
    # Try streaming first; if it throws (e.g., 504), fall back to non-streaming with retries
    try:
        stream = model.generate_content(
            [instruction, file],
            stream=True,
            generation_config=_gen_config_json(),
        )
        collected: List[str] = []
        try:
            for chunk in stream:
                try:
                    t = getattr(chunk, "text", None) or ""
                except Exception:
                    t = ""
                if t:
                    collected.append(t)
                    if console and stream_console:
                        try:
                            console.out.write(t)
                            console.out.flush()
                        except Exception:
                            try:
                                console.print(t)
                            except Exception:
                                pass
        except Exception as e:  # streaming failed mid-way
            if console and stream_console:
                try:
                    console.log(f"Streaming failed, will retry without streaming: {e}")
                except Exception:
                    pass
            collected = []
        # best-effort newline after streaming
        if console and stream_console:
            try:
                console.out.write("\n")
                console.out.flush()
            except Exception:
                try:
                    console.print("")
                except Exception:
                    pass
        text = ("".join(collected)).strip()
    except Exception as e:
        if console and stream_console:
            try:
                console.log(f"Stream request failed to start, will retry without streaming: {e}")
            except Exception:
                pass
    if not text:
        if console and stream_console:
            try:
                console.log("Retrying sections request without streaming (with backoff)…")
            except Exception:
                pass
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = model.generate_content(
                    [instruction, file],
                    generation_config=_gen_config_json(),
                )
                text = _extract_text_from_response(resp).strip()
                if text:
                    break
            except Exception as e:
                last_err = e
            # backoff before next try
            time.sleep(2 ** attempt)
        if not text and last_err:
            raise last_err
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_with_repair(text)
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise RuntimeError("Gemini did not return a valid sections JSON with 'sections'.")
    return data["sections"]


def get_section_content_verbose(
    model: genai.GenerativeModel,
    pdf_path: Optional[str] = None,
    *,
    uploaded_file: Any | None = None,
    section_index: int,
    section_type: str,
    section_title: str,
    console=None,
    debug_path: Optional[str] = None,
    stream_console: bool = False,
) -> Dict[str, Any]:
    """Request a single section's content as XHTML JSON."""
    if console:
        try:
            console.log(f"Requesting section {section_index} [{section_type}]: {section_title}")
        except Exception:
            pass
    file = uploaded_file or (
        genai.upload_file(pdf_path, mime_type="application/pdf") if pdf_path else None
    )
    if file is None:
        raise ValueError("No uploaded_file provided and no pdf_path to upload from.")
    safe_title = section_title.replace("{", "{{").replace("}", "}}")
    safe_type = (section_type or "section").strip()
    instruction = (
        "Extract the specified book section from the PDF and return JSON only. "
        "JSON shape: {\"xhtml\": string, \"images\": [ {\"filename\": string, \"label\": string, \"box_2d\": [x0,y0,x1,y1], \"page_index\": integer (1-based) } ] }.\n"
        "Rules: xhtml must be a well-formed XHTML 1.1 fragment. Use <ul><li> for lists; do NOT use <ol>. Preserve original numbering/prefix characters by including them inside the <li> text. "
        "Close all tags; use </p><p>, never </p<p>. For empty elements like <br>, output <br />. Where images belong, include <figure><img src=\"images/{filename}\" alt=\"{label}\"/></figure> with the provided filename.\n"
        "Only include semantically meaningful figures (photos, diagrams, charts, illustrations).\n"
        "Explicitly DO NOT include decorative elements like borders, underlines, highlights, separators, simple rectangles/boxes around text, or page ornaments.\n"
        "Preserve the original numbering and labels in headings exactly as they appear (e.g., 'Chapter 3', '1.2.4 Methods'). Do not renumber based on the provided index.\n"
        "box_2d coordinates MUST be normalized floats in [0,1] relative to the page (top-left origin).\n"
        "Do NOT use HTML named entities like &copy;, &nbsp;, &mdash;; prefer literal Unicode symbols (©, —) or numeric character references (e.g., &#169;). The only allowed named entities are &amp;, &lt;, &gt;, &quot;, &apos;.\n"
        "CRITICAL: The xhtml field is a JSON string value. You MUST JSON-escape it correctly: escape embedded double quotes as \\\" and backslashes as \\\\, encode newlines as \\n, and do not emit raw control characters. "
        "Return strict RFC 8259 JSON only (no trailing commas, comments, code fences, or backticks). "
        f"Return only JSON. Section to extract: index={section_index}, type=\"{safe_type}\", title=\"{safe_title}\"."
    )
    text = ""
    try:
        stream = model.generate_content(
            [instruction, file],
            stream=True,
            generation_config=_gen_config_json(),
        )
        if console and stream_console:
            try:
                console.log("Streaming section content from Gemini…")
            except Exception:
                pass
        collected: List[str] = []
        try:
            for chunk in stream:
                try:
                    t = getattr(chunk, "text", None) or ""
                except Exception:
                    t = ""
                if t:
                    collected.append(t)
                    if console and stream_console:
                        try:
                            console.out.write(t)
                            console.out.flush()
                        except Exception:
                            try:
                                console.print(t)
                            except Exception:
                                pass
        except Exception as e:
            if console and stream_console:
                try:
                    console.log(f"Streaming failed, will retry without streaming: {e}")
                except Exception:
                    pass
            collected = []
        if console and stream_console:
            try:
                console.out.write("\n")
                console.out.flush()
            except Exception:
                try:
                    console.print("")
                except Exception:
                    pass
        text = ("".join(collected)).strip()
    except Exception as e:
        if console and stream_console:
            try:
                console.log(f"Stream request failed to start, will retry without streaming: {e}")
            except Exception:
                pass
    if not text:
        if console and stream_console:
            try:
                console.log("Retrying section request without streaming (with backoff)…")
            except Exception:
                pass
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = model.generate_content(
                    [instruction, file],
                    generation_config=_gen_config_json(),
                )
                text = _extract_text_from_response(resp).strip()
                if text:
                    break
            except Exception as e:
                last_err = e
            time.sleep(2 ** attempt)
        if not text and last_err:
            raise last_err
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_with_repair(text)
    if not isinstance(data, dict):
        raise RuntimeError("Gemini did not return a valid section JSON object.")
    xhtml = (
        data.get("xhtml")
        or data.get("html")
        or data.get("content")
        or data.get("section_html")
        or ""
    )
    images = data.get("images")
    if not isinstance(images, list):
        images = []
    # Normalize image items
    norm_images: List[Dict[str, Any]] = []
    for it in images:
        if not isinstance(it, dict):
            continue
        filename = it.get("filename") or it.get("file") or it.get("name")
        label = it.get("label") or it.get("alt") or ""
        box = it.get("box_2d") or it.get("bbox") or it.get("box")
        page_index = it.get("page_index") or it.get("page") or it.get("pageNumber")
        if not filename or not isinstance(filename, str):
            continue
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(v) for v in box]
        except Exception:
            continue
        # clamp to [0,1]
        def _clamp(v: float) -> float:
            return 0.0 if v < 0 else (1.0 if v > 1 else v)
        x0, y0, x1, y1 = _clamp(x0), _clamp(y0), _clamp(x1), _clamp(y1)
        if page_index is None:
            continue
        try:
            page_index = int(page_index)
        except Exception:
            continue
        norm_images.append({
            "filename": filename,
            "label": label,
            "box_2d": [x0, y0, x1, y1],
            "page_index": page_index,
        })
    return {"xhtml": xhtml, "images": norm_images}


def get_book_metadata_verbose(
    model: genai.GenerativeModel,
    pdf_path: Optional[str] = None,
    *,
    uploaded_file: Any | None = None,
    console=None,
    debug_path: Optional[str] = None,
    stream_console: bool = False,
) -> Dict[str, Any]:
    """Ask Gemini to extract bibliographic metadata."""
    if console:
        try:
            console.log("Requesting book metadata from Gemini…")
        except Exception:
            pass
    file = uploaded_file or (
        genai.upload_file(pdf_path, mime_type="application/pdf") if pdf_path else None
    )
    if file is None:
        raise ValueError("No uploaded_file provided and no pdf_path to upload from.")
    instruction = (
        "Extract bibliographic metadata for this book and return JSON only. "
        "Fields: title (string); authors (array of strings); isbn (string, digits/dashes, null if none); "
        "language (ISO 639-1 like 'en' if known); publisher (string); date (YYYY or YYYY-MM or YYYY-MM-DD); "
        "description (string summary); subjects (array of strings). Use straight ASCII quotes (\" and ') in text fields; avoid &ldquo; &rdquo; &lsquo; &rsquo;. "
        "Output MUST be strict RFC 8259 JSON: double-quote all keys/strings, no trailing commas, no comments/backticks/fences."
    )
    text = ""
    try:
        stream = model.generate_content(
            [instruction, file],
            stream=True,
            generation_config=_gen_config_json(),
        )
        collected: List[str] = []
        try:
            for chunk in stream:
                try:
                    t = getattr(chunk, "text", None) or ""
                except Exception:
                    t = ""
                if t:
                    collected.append(t)
                    if console and stream_console:
                        try:
                            console.out.write(t)
                            console.out.flush()
                        except Exception:
                            try:
                                console.print(t)
                            except Exception:
                                pass
        except Exception as e:
            if console and stream_console:
                try:
                    console.log(f"Streaming failed, will retry without streaming: {e}")
                except Exception:
                    pass
            collected = []
        if console and stream_console:
            try:
                console.out.write("\n")
                console.out.flush()
            except Exception:
                try:
                    console.print("")
                except Exception:
                    pass
        text = ("".join(collected)).strip()
    except Exception as e:
        if console and stream_console:
            try:
                console.log(f"Stream request failed to start, will retry without streaming: {e}")
            except Exception:
                pass
    if not text:
        if console and stream_console:
            try:
                console.log("Retrying metadata request without streaming (with backoff)…")
            except Exception:
                pass
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = model.generate_content(
                    [instruction, file],
                    generation_config=_gen_config_json(),
                )
                text = _extract_text_from_response(resp).strip()
                if text:
                    break
            except Exception as e:
                last_err = e
            time.sleep(2 ** attempt)
        if not text and last_err:
            raise last_err
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(text, encoding="utf-8")
        except Exception:
            pass
    data = _parse_json_with_repair(text)
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


_JSON_WS_RE = re.compile(r"(?<=\d)[\s\r\n]+(?=\d)")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x09\x0B\x0C\x0E-\x1F]")
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})

def _clean_json_for_strict_parsing(text: str) -> str:
    s = text.strip()
    # Normalize quotes that can break JSON
    s = s.translate(_SMART_QUOTES)
    # Remove control characters
    s = _CTRL_CHARS_RE.sub("", s)
    # Merge numbers accidentally split by whitespace/newlines during streaming
    s = _JSON_WS_RE.sub("", s)
    # Remove trailing commas before } or ]
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    return s

def _parse_json_with_repair(text: str) -> Any:
    # Fast path
    try:
        return json.loads(text)
    except Exception:
        pass
    # Clean and retry
    s = _clean_json_for_strict_parsing(text)
    try:
        return json.loads(s)
    except Exception:
        # Try to bracket-match the largest JSON object/array in the string
        start_obj, end_obj = s.find("{"), s.rfind("}")
        start_arr, end_arr = s.find("["), s.rfind("]")
        candidates = []
        if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
            candidates.append(s[start_obj : end_obj + 1])
        if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
            candidates.append(s[start_arr : end_arr + 1])
        for cand in candidates:
            try:
                return json.loads(cand)
            except Exception:
                continue
        # Last resort: raise original error with some context
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
