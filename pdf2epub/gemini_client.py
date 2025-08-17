from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path as _Path
from typing import Any, Dict, Iterable, List, Optional

import google.generativeai as genai

SYSTEM_PROMPT = (
    "You are a helpful editor. Clean and structure book-like text into chapters and sections. "
    "Preserve lists, code blocks, and headings. Output MUST be valid HTML5 fragment(s), "
    "using <h1> for title, <h2>/<h3> for sections, <p> for paragraphs, <ul>/<ol>/<li> for lists, "
    "and <pre><code> for code. Always preserve numbering prefixes and labels as they appear in the document "
    "(e.g., 'Chapter 3', '1.2.4', '§ 2.3'). When a prompt explicitly requests images, include them as instructed; "
    "otherwise do not add images."
)

# Conservative default to avoid oversized responses; callers can override if needed
def _max_output_tokens() -> int:
    try:
        v = int(os.environ.get("PDF2EPUB_MAX_OUTPUT_TOKENS", "2048"))
        if v <= 0:
            return 2048
        return v
    except Exception:
        return 2048


def _json_cfg() -> Dict[str, Any]:
    return {"response_mime_type": "application/json", "max_output_tokens": _max_output_tokens()}


# ---- Upload caching (avoid re-uploading the same PDF) ----
def _cache_dir() -> _Path:
    env = os.environ.get("PDF2EPUB_CACHE_DIR")
    if env:
        base = _Path(env)
    elif sys.platform == "darwin":  # type: ignore[attr-defined]
        base = _Path.home() / "Library" / "Caches" / "pdf2epub"
    elif os.environ.get("XDG_CACHE_HOME"):
        base = _Path(os.environ["XDG_CACHE_HOME"]) / "pdf2epub"
    else:
        base = _Path.home() / ".cache" / "pdf2epub"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_file() -> _Path:
    return _cache_dir() / "files.json"


def _load_cache() -> Dict[str, Any]:
    p = _cache_file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(d: Dict[str, Any]) -> None:
    _cache_file().write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _wait_active(name: str, timeout_s: int = 120) -> None:
    start = time.time()
    while True:
        try:
            f = genai.get_file(name)
            state = getattr(f, "state", None)
            if state == "ACTIVE" or getattr(state, "name", None) == "ACTIVE":
                return
        except Exception:
            pass
        if time.time() - start > timeout_s:
            raise TimeoutError("Timeout waiting for file to become ACTIVE")
        time.sleep(1.5)


def ensure_uploaded_file(pdf_path: str, *, console=None, use_cache: bool = True):
    """Return a Gemini File object, reusing an existing upload by PDF hash when possible."""
    sha = _sha256(pdf_path)
    cache = _load_cache() if use_cache else {}
    entry = cache.get(sha) if isinstance(cache, dict) else None
    if entry and isinstance(entry, dict):
        name = entry.get("name")
        if isinstance(name, str) and name:
            try:
                f = genai.get_file(name)
                state = getattr(f, "state", None)
                if state != "FAILED":
                    if console:
                        try:
                            console.log(f"Reusing uploaded PDF: {name}")
                        except Exception:
                            pass
                    return f
            except Exception:
                pass
    if console:
        try:
            console.log("Uploading PDF once and caching handle…")
        except Exception:
            pass
    f = genai.upload_file(pdf_path, mime_type="application/pdf")
    _wait_active(getattr(f, "name", ""))
    if use_cache:
        cache[sha] = {"name": getattr(f, "name", ""), "uploaded_at": int(time.time())}
        _save_cache(cache)
    return f


def init_client(api_key: str, model: str) -> genai.GenerativeModel:
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name=model, system_instruction=SYSTEM_PROMPT)


def generate_structured_html(model: genai.GenerativeModel, chunks: Iterable[str]) -> list[str]:
    html_parts: list[str] = []
    for chunk in chunks:
        prompt = (
            "Convert the following PDF-extracted text into clean, structured HTML suitable for EPUB.\n\n"
            f"TEXT:\n{chunk}\n\n"
            "Rules: produce only HTML, no markdown, no frontmatter, no explanations."
        )
        resp = model.generate_content(prompt)
        html = resp.text or ""
        html_parts.append(html.strip())
    return html_parts


def get_sections_from_pdf_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    uploaded_file=None,
    console=None,
    debug_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Ask Gemini to enumerate all logical sections in reading order."""
    if console:
        try:
            console.log("Requesting sections (front matter, chapters, back matter) from Gemini…")
        except Exception:
            pass
    file = uploaded_file or genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Analyze this PDF and return JSON with all logical sections in reading order. "
        "Return only JSON with shape: {\"sections\": [{\"index\": integer starting at 1, \"type\": one of "
        "'title','copyright','dedication','preface','foreword','prologue','introduction','toc','chapter','appendix','acknowledgments','epilogue','afterword','notes','glossary','bibliography','index', "
        "\"title\": string, \"page_start\": integer (1-based), \"page_end\": integer (1-based, inclusive) }]}. "
        "Important: the \"title\" must preserve any numbering and labels exactly as in the document (e.g., 'Chapter 3', '1.1 Overview', '§ 2.3'). "
        "Provide page ranges as a best-effort estimate for where each logical section occurs. Do not include full content here."
    )
    chat = model.start_chat()
    resp = chat.send_message([instruction, file], generation_config=_json_cfg())
    text = _extract_text_from_response(resp).strip()
    finish = _get_finish_reason(resp)
    sections: List[Dict[str, Any]] = []
    try:
        data = _parse_json_safely(text)
        if isinstance(data, dict) and isinstance(data.get("sections"), list):
            sections.extend([x for x in data["sections"] if isinstance(x, dict)])
    except Exception:
        pass
    tries = 0
    while finish == "MAX_TOKENS" and tries < 3:
        tries += 1
        cont = (
            "Continue from the last sentence. Return only JSON with the same shape, containing only the remaining "
            "sections (no repetition)."
        )
        resp = chat.send_message(cont, generation_config=_json_cfg())
        text = _extract_text_from_response(resp).strip()
        finish = _get_finish_reason(resp)
        try:
            data = _parse_json_safely(text)
            if isinstance(data, dict) and isinstance(data.get("sections"), list):
                sections.extend([x for x in data["sections"] if isinstance(x, dict)])
        except Exception:
            continue
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(json.dumps({"sections": sections}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    if not sections:
        raise RuntimeError("Gemini did not return a valid sections JSON with 'sections'.")
    return sections


def get_section_content_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    uploaded_file=None,
    section_index: int,
    section_type: str,
    section_title: str,
    page_range: Optional[tuple[int, int]] = None,
    console=None,
    debug_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Request a single section's content as XHTML JSON with continuation on MAX_TOKENS."""
    if console:
        try:
            console.log(f"Requesting section {section_index} [{section_type}]: {section_title}")
        except Exception:
            pass
    file = uploaded_file or genai.upload_file(pdf_path, mime_type="application/pdf")
    safe_title = section_title.replace("{", "{{").replace("}", "}}")
    safe_type = (section_type or "section").strip()
    instruction = (
        "Extract the specified book section from the PDF and return JSON only. "
        "JSON shape: {\"xhtml\": string, \"images\": [ {\"filename\": string, \"label\": string, \"box_2d\": [x0,y0,x1,y1], \"page_index\": integer (1-based) } ] }.\n"
        "Rules: xhtml should be a clean HTML5 fragment. Where images belong, include <figure><img src=\"images/{filename}\" alt=\"{label}\"/></figure> with the provided filename.\n"
        "Only include semantically meaningful figures (photos, diagrams, charts, illustrations).\n"
        "Explicitly DO NOT include decorative elements like borders, underlines, highlights, separators, simple rectangles/boxes around text, or page ornaments.\n"
        "Preserve the original numbering and labels in headings exactly as they appear (e.g., 'Chapter 3', '1.2.4 Methods'). Do not renumber based on the provided index.\n"
        "box_2d coordinates MUST be normalized floats in [0,1] relative to the page (top-left origin).\n"
        f"Return only JSON. Section to extract: index={section_index}, type=\"{safe_type}\", title=\"{safe_title}\"."
    )
    if page_range and isinstance(page_range, tuple) and len(page_range) == 2:
        p0, p1 = page_range
        try:
            p0i = int(p0)
            p1i = int(p1)
            instruction += f" Restrict extraction strictly to pages {p0i}..{p1i} (inclusive)."
        except Exception:
            pass
    # Chat-based loop with continuation
    chat = model.start_chat()
    resp = chat.send_message([instruction, file], generation_config=_json_cfg())
    text = _extract_text_from_response(resp).strip()
    finish = _get_finish_reason(resp)
    xhtml_accum: List[str] = []
    images_accum: List[Dict[str, Any]] = []
    try:
        data = _parse_json_safely(text)
        if isinstance(data, dict):
            xhtml_accum.append(
                data.get("xhtml")
                or data.get("html")
                or data.get("content")
                or data.get("section_html")
                or ""
            )
            images_accum.extend(_normalize_images(data.get("images")))
    except Exception:
        pass
    # Continue if truncated
    tries = 0
    while finish == "MAX_TOKENS" and tries < 3:
        tries += 1
        cont = (
            "Continue from the last sentence. Return only JSON with the same shape, containing only the remaining "
            "content for this section (no repetition)."
        )
        resp = chat.send_message(cont, generation_config=_json_cfg())
        text = _extract_text_from_response(resp).strip()
        finish = _get_finish_reason(resp)
        try:
            data = _parse_json_safely(text)
            if isinstance(data, dict):
                xhtml_accum.append(
                    data.get("xhtml")
                    or data.get("html")
                    or data.get("content")
                    or data.get("section_html")
                    or ""
                )
                images_accum.extend(_normalize_images(data.get("images")))
        except Exception:
            continue
    xhtml = "\n".join([s for s in xhtml_accum if isinstance(s, str)])
    if debug_path:
        try:
            from pathlib import Path
            Path(debug_path).write_text(json.dumps({"xhtml": xhtml, "images": images_accum}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return {"xhtml": xhtml, "images": images_accum}


def get_book_metadata_verbose(
    model: genai.GenerativeModel,
    pdf_path: str,
    *,
    uploaded_file=None,
    console=None,
    debug_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Ask Gemini to extract bibliographic metadata."""
    if console:
        try:
            console.log("Requesting book metadata from Gemini…")
        except Exception:
            pass
    file = uploaded_file or genai.upload_file(pdf_path, mime_type="application/pdf")
    instruction = (
        "Extract bibliographic metadata for this book and return JSON only. "
        "Fields: title (string); authors (array of strings); isbn (string, digits/dashes, null if none); "
        "language (ISO 639-1 like 'en' if known); publisher (string); date (YYYY or YYYY-MM or YYYY-MM-DD); "
        "description (string summary); subjects (array of strings)."
    )
    text = ""
    try:
        stream = model.generate_content(
            [instruction, file],
            stream=True,
            generation_config=_json_cfg(),
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
                    if console:
                        try:
                            console.out.write(t)
                            console.out.flush()
                        except Exception:
                            try:
                                console.print(t)
                            except Exception:
                                pass
        except Exception as e:
            if console:
                try:
                    console.log(f"Streaming failed, will retry without streaming: {e}")
                except Exception:
                    pass
            collected = []
        if console:
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
        if console:
            try:
                console.log(f"Stream request failed to start, will retry without streaming: {e}")
            except Exception:
                pass
    if not text:
        if console:
            try:
                console.log("Retrying metadata request without streaming (with backoff)…")
            except Exception:
                pass
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = model.generate_content(
                    [instruction, file],
                    generation_config=_json_cfg(),
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


def _get_finish_reason(resp) -> Optional[str]:
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return None
        fr = getattr(cands[0], "finish_reason", None)
        if isinstance(fr, str):
            return fr
        name = getattr(fr, "name", None)
        if isinstance(name, str):
            return name
    except Exception:
        return None
    return None


def _normalize_images(images: Any) -> List[Dict[str, Any]]:
    if not isinstance(images, list):
        return []
    out: List[Dict[str, Any]] = []
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
        def _clamp(v: float) -> float:
            return 0.0 if v < 0 else (1.0 if v > 1 else v)
        x0, y0, x1, y1 = _clamp(x0), _clamp(y0), _clamp(x1), _clamp(y1)
        if page_index is None:
            continue
        try:
            page_index = int(page_index)
        except Exception:
            continue
        out.append({
            "filename": filename,
            "label": label,
            "box_2d": [x0, y0, x1, y1],
            "page_index": page_index,
        })
    return out
