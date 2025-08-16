from __future__ import annotations

from typing import Iterable

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
