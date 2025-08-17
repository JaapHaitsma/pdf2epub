from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
import time

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
    console=None,
    debug_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Ask Gemini to enumerate all logical sections in reading order."""
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
    text = ""
    # Try streaming first; if it throws (e.g., 504), fall back to non-streaming with retries
    try:
        stream = model.generate_content(
            [instruction, file],
            stream=True,
            generation_config={"response_mime_type": "application/json"},
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
        except Exception as e:  # streaming failed mid-way
            if console:
                try:
                    console.log(f"Streaming failed, will retry without streaming: {e}")
                except Exception:
                    pass
            collected = []
        # best-effort newline after streaming
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
                console.log("Retrying sections request without streaming (with backoff)…")
            except Exception:
                pass
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = model.generate_content(
                    [instruction, file],
                    generation_config={"response_mime_type": "application/json"},
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
    """Request a single section's content as XHTML JSON."""
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
        '- "xhtml": a complete HTML5 fragment for this section (no scripts). \n'
        "Do not include images or <img> tags; summarize any figure captions in text. \n"
        f"Return only JSON. Section to extract: index={section_index}, type=\"{safe_type}\", title=\"{safe_title}\"."
    )
    text = ""
    try:
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
                console.log("Retrying section request without streaming (with backoff)…")
            except Exception:
                pass
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = model.generate_content(
                    [instruction, file],
                    generation_config={"response_mime_type": "application/json"},
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
    """Ask Gemini to extract bibliographic metadata."""
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
    text = ""
    try:
        stream = model.generate_content(
            [instruction, file],
            stream=True,
            generation_config={"response_mime_type": "application/json"},
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
                    generation_config={"response_mime_type": "application/json"},
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
