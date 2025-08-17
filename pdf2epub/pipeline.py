from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from .gemini_client import (
    get_book_metadata_verbose,
    get_section_content_verbose,
    get_sections_from_pdf_verbose,
    init_client,
)
from .packager import write_manifest_to_dir, zip_epub_from_dir


def convert_pdf_to_epub(
    input_pdf: Path,
    output_epub: Path,
    api_key: str,
    model: str,
    keep_sources: bool = False,
    console: Optional[Console] = None,
    by_section: bool = False,
    debug: bool = False,
) -> None:
    console = console or Console()

    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)

    console.log(f"Reading PDF: {input_pdf}")
    client = init_client(api_key=api_key, model=model)

    # Only section-by-section mode is supported
    console.log("Using section-by-section mode…")
    manifest = _build_manifest_by_section(client, input_pdf, output_epub, console, debug)
    temp_dir = output_epub.parent / (output_epub.stem + "_epub_src")
    if temp_dir.exists():
        # Best-effort clean
        import shutil
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    write_manifest_to_dir(manifest, temp_dir)
    console.log("Zipping EPUB…")
    zip_epub_from_dir(temp_dir, output_epub)
    if not keep_sources:
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except Exception:
            pass
    return


def _build_manifest_by_section(
    client, input_pdf: Path, output_epub: Path, console: Console, debug: bool
) -> Dict[str, Any]:
    stem = output_epub.stem
    # 1) Get sections
    sections_debug = output_epub.parent / f"{stem}_sections_raw.json"
    sections = get_sections_from_pdf_verbose(
        client,
        str(input_pdf),
        console=console,
        debug_path=str(sections_debug) if debug else None,
    )
    if not sections:
        # Fallback to a single generic section
        sections = [{"index": 1, "type": "section", "title": input_pdf.stem}]

    files: List[Dict[str, Any]] = []
    entries: List[Dict[str, str]] = []

    css = (
        "body{font-family:serif;line-height:1.5;} h1,h2,h3{font-family:sans-serif;} "
        "img{max-width:100%; height:auto;} code,pre{font-family:monospace;}"
    )
    files.append({"path": "OEBPS/styles.css", "content": css, "encoding": "utf-8"})

    # Track how many times we've seen a given base name to avoid duplicates
    used_counts: Dict[str, int] = {}
    for sec in sections:
        idx = int(sec.get("index", len(entries) + 1))
        sec_type = str(sec.get("type", "section")).lower()
        title = str(sec.get("title", sec_type.title()))
        sec_debug = output_epub.parent / f"{stem}_sec{idx:02}_raw.json"
        data = get_section_content_verbose(
            client,
            str(input_pdf),
            section_index=idx,
            section_type=sec_type,
            section_title=title,
            console=console,
            debug_path=str(sec_debug) if debug else None,
        )
        html_fragment: str = data.get("xhtml", "")
        xhtml = _wrap_xhtml(title, html_fragment)
        base = _basename_from_title_or_type(title, sec_type, idx, used_counts)
        rel_href = f"{base}.xhtml"
        file_path = f"OEBPS/{rel_href}"
        files.append({"path": file_path, "content": xhtml, "encoding": "utf-8"})
        entries.append({"id": f"sec{idx:02}", "href": rel_href, "title": title, "type": sec_type})

    book_title = input_pdf.stem
    meta = _extract_metadata(client, input_pdf, output_epub, console, debug)
    nav = _build_nav_xhtml(book_title, entries)
    uid = meta.get("isbn") or "urn:uuid:00000000-0000-0000-0000-000000000000"
    ncx = _build_toc_ncx(uid, book_title, entries)
    opf = _build_content_opf(book_title, entries, meta, include_ncx=True)
    files.append({"path": "OEBPS/nav.xhtml", "content": nav, "encoding": "utf-8"})
    files.append({"path": "OEBPS/toc.ncx", "content": ncx, "encoding": "utf-8"})
    files.append({"path": "OEBPS/content.opf", "content": opf, "encoding": "utf-8"})

    return {"files": files}


def _wrap_xhtml(title: str, body_fragment: str) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml' xml:lang='en'>\n"
        "  <head>\n"
        f"    <title>{_escape_xml(title)}</title>\n"
        "    <meta charset='utf-8'/>\n"
        "    <link rel='stylesheet' type='text/css' href='styles.css'/>\n"
        "  </head>\n"
        "  <body>\n"
        f"{body_fragment}\n"
        "  </body>\n"
        "</html>\n"
    )


def _build_nav_xhtml(book_title: str, entries: List[Dict[str, str]]) -> str:
    lis = "\n".join(
        f"        <li><a href='{_escape_xml(ch['href'])}'>{_escape_xml(ch['title'])}</a></li>" for ch in entries
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>\n"
        "  <head>\n"
        f"    <title>{_escape_xml(book_title)}</title>\n"
        "    <meta charset='utf-8'/>\n"
        "  </head>\n"
        "  <body>\n"
        "    <nav epub:type='toc' id='toc'>\n"
        f"      <h2>{_escape_xml(book_title)}</h2>\n"
        "      <ol>\n"
        f"{lis}\n"
        "      </ol>\n"
        "    </nav>\n"
        "  </body>\n"
        "</html>\n"
    )


def _build_content_opf(book_title: str, entries: List[Dict[str, str]], meta: Dict[str, Any] | None = None, include_ncx: bool = False) -> str:
    meta = meta or {}
    dc_creators = "\n".join(
        f"    <dc:creator>{_escape_xml(a)}</dc:creator>" for a in meta.get("authors", [])
    )
    dc_subjects = "\n".join(
        f"    <dc:subject>{_escape_xml(s)}</dc:subject>" for s in meta.get("subjects", [])
    )
    dc_identifier = (
        f"    <dc:identifier id='bookid'>{_escape_xml(meta['isbn'])}</dc:identifier>\n"
        if meta.get("isbn") else "    <dc:identifier id='bookid'>urn:uuid:00000000-0000-0000-0000-000000000000</dc:identifier>\n"
    )
    dc_language = _escape_xml(meta.get("language") or "en")
    dc_publisher = (
        f"    <dc:publisher>{_escape_xml(meta['publisher'])}</dc:publisher>\n" if meta.get("publisher") else ""
    )
    dc_date = (
        f"    <dc:date>{_escape_xml(meta['date'])}</dc:date>\n" if meta.get("date") else ""
    )
    dc_description = (
        f"    <dc:description>{_escape_xml(meta['description'])}</dc:description>\n" if meta.get("description") else ""
    )
    manifest_items = [
        ("    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml'/>") if include_ncx
        else ("    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>")
        ,
        "    <item id='css' href='styles.css' media-type='text/css'/>",
    ]
    if include_ncx:
        manifest_items.insert(0, "    <item id='ncx' href='toc.ncx' media-type='application/x-dtbncx+xml'/>")
    spine_items = []
    for ch in entries:
        chap_id = ch["id"]
        href = ch["href"]
        manifest_items.append(
            f"    <item id='{chap_id}' href='{_escape_xml(href)}' media-type='application/xhtml+xml'/>"
        )
        spine_items.append(f"    <itemref idref='{chap_id}'/>")
    manifest_xml = "\n".join(manifest_items)
    spine_xml = "\n".join(spine_items)
    pkg_version = "2.0" if include_ncx else "3.0"
    spine_open = "  <spine toc='ncx'>\n" if include_ncx else "  <spine>\n"
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        f"<package xmlns='http://www.idpf.org/2007/opf' version='{pkg_version}' unique-identifier='bookid'>\n"
        "  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>\n"
        f"    <dc:title>{_escape_xml(meta.get('title') or book_title)}</dc:title>\n"
        f"{dc_creators}\n"
        f"{dc_identifier}"
        f"    <dc:language>{dc_language}</dc:language>\n"
        f"{dc_publisher}"
        f"{dc_date}"
        f"{dc_description}"
        f"{dc_subjects}\n"
        "    <meta property='dcterms:modified'>1970-01-01T00:00:00Z</meta>\n"
        "  </metadata>\n"
        "  <manifest>\n"
        f"{manifest_xml}\n"
        "  </manifest>\n"
        f"{spine_open}"
        f"{spine_xml}\n"
        "  </spine>\n"
        "</package>\n"
    )


def _build_toc_ncx(uid: str, book_title: str, entries: List[Dict[str, str]]) -> str:
    navpoints = []
    for i, ch in enumerate(entries, 1):
        title = _escape_xml(ch["title"]) if ch.get("title") else f"Section {i}"
        href = _escape_xml(ch["href"])
        navpoints.append(
            "      <navPoint id='navPoint-{i}' playOrder='{i}'>\n"
            f"        <navLabel><text>{title}</text></navLabel>\n"
            f"        <content src='{href}'/>\n"
            "      </navPoint>\n".replace("{i}", str(i))
        )
    navpoints_xml = "".join(navpoints)
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'>\n"
        "  <head>\n"
        f"    <meta name='dtb:uid' content='{_escape_xml(uid)}'/>\n"
        "    <meta name='dtb:depth' content='1'/>\n"
        "    <meta name='dtb:totalPageCount' content='0'/>\n"
        "    <meta name='dtb:maxPageNumber' content='0'/>\n"
        "  </head>\n"
        "  <docTitle><text>" + _escape_xml(book_title) + "</text></docTitle>\n"
        "  <navMap>\n"
        f"{navpoints_xml}"
        "  </navMap>\n"
        "</ncx>\n"
    )


def _extract_metadata(client, input_pdf: Path, output_epub: Path, console: Console, debug: bool) -> Dict[str, Any]:
    try:
        dbg_path = output_epub.parent / f"{output_epub.stem}_metadata_raw.json"
        return get_book_metadata_verbose(
            client,
            str(input_pdf),
            console=console,
            debug_path=str(dbg_path) if debug else None,
        )
    except Exception as e:  # noqa: BLE001
        try:
            console.log(f"Metadata extraction failed, continuing without: {e}")
        except Exception:
            pass
        return {}


def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _slugify(text: str) -> str:
    s = (text or "").strip().lower()
    out = []
    prev_hyphen = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_hyphen = False
        else:
            if not prev_hyphen:
                out.append("-")
                prev_hyphen = True
    slug = "".join(out).strip("-")
    return slug or "section"


def _basename_from_title_or_type(title: str, sec_type: str, idx: int, used_counts: Dict[str, int]) -> str:
    """Derive file basename from Gemini's section title; ensure uniqueness.

    Rules:
    - Primary: slugified title from Gemini. If empty, fallback to slugified type.
    - If still empty, fallback to "section".
    - If the same basename repeats, append a numeric suffix starting at -01, then -02, etc.
      The very first occurrence has no suffix.
    """
    base = _slugify(title) if title else ""
    if not base:
        base = _slugify(sec_type) if sec_type else "section"
    if not base:
        base = "section"
    count = used_counts.get(base, 0) + 1
    used_counts[base] = count
    if count == 1:
        return base
    # First duplicate gets -01, then -02, ...
    return f"{base}-{count - 1:02}"
