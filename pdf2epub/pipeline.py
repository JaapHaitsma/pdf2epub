from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from .gemini_client import (
    get_book_metadata_verbose,
    get_chapter_content_verbose,
    get_section_content_verbose,
    get_sections_from_pdf_verbose,
    get_toc_from_pdf_verbose,
    init_client,
    upload_pdf_and_request_epub_manifest_verbose,
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
) -> None:
    console = console or Console()

    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)

    console.log(f"Reading PDF: {input_pdf}")
    client = init_client(api_key=api_key, model=model)

    used_mode = "manifest"
    if by_section:
        console.log("Using section-by-section mode…")
        manifest = _build_manifest_by_section(client, input_pdf, output_epub, console)
        used_mode = "section"
    else:
        console.log("Delegating EPUB generation to Gemini (manifest mode)…")
        try:
            debug_json = (output_epub.parent / (output_epub.stem + "_manifest_raw.json"))
            try:
                manifest = upload_pdf_and_request_epub_manifest_verbose(
                    client,
                    str(input_pdf),
                    console=console,
                    debug_path=str(debug_json),
                )
            except TypeError:
                # Back-compat for tests or older mocks that don't accept kwargs
                manifest = upload_pdf_and_request_epub_manifest_verbose(client, str(input_pdf))
        except Exception as e:
            console.print(f"[yellow]Manifest mode failed:[/] {e}")
            console.log("Falling back to section-by-section mode…")
            manifest = _build_manifest_by_section(client, input_pdf, output_epub, console)
            used_mode = "section"
    temp_dir = output_epub.parent / (output_epub.stem + "_epub_src")
    if temp_dir.exists():
        # Best-effort clean
        import shutil
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    write_manifest_to_dir(manifest, temp_dir)
    # If we used manifest mode, try to patch OPF with extracted metadata
    if used_mode == "manifest":
        meta = _extract_metadata(client, input_pdf, output_epub, console)
        if meta:
            _patch_opf_in_dir(temp_dir, meta, console)
    console.log("Zipping EPUB…")
    zip_epub_from_dir(temp_dir, output_epub)
    if not keep_sources:
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except Exception:
            pass
    return


def _build_manifest_by_chapter(
    client, input_pdf: Path, output_epub: Path, console: Console
) -> Dict[str, Any]:
    stem = output_epub.stem
    # 1) Get TOC
    toc_debug = output_epub.parent / f"{stem}_toc_raw.json"
    chapters = get_toc_from_pdf_verbose(
        client, str(input_pdf), console=console, debug_path=str(toc_debug)
    )
    # Fallback if empty TOC: assume a single chapter titled by filename
    if not chapters:
        chapters = [{"index": 1, "title": input_pdf.stem}]

    # 2) Fetch each chapter content
    chapter_entries: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []

    # Basic stylesheet
    css = (
        "body{font-family:serif;line-height:1.5;} h1,h2,h3{font-family:sans-serif;} "
        "img{max-width:100%; height:auto;} code,pre{font-family:monospace;}"
    )
    files.append({"path": "OEBPS/styles.css", "content": css, "encoding": "utf-8"})

    for chap in chapters:
        idx = int(chap.get("index", len(chapter_entries) + 1))
        title = str(chap.get("title", f"Chapter {idx}"))
        ch_debug = output_epub.parent / f"{stem}_ch{idx:02}_raw.json"
        data = get_chapter_content_verbose(
            client,
            str(input_pdf),
            chapter_index=idx,
            chapter_title=title,
            console=console,
            debug_path=str(ch_debug),
        )
        html_fragment: str = data.get("xhtml", "")
    xhtml = _wrap_xhtml(title, html_fragment)
    rel_href = f"chapter-{idx:02}.xhtml"
    file_path = f"OEBPS/{rel_href}"
    files.append({"path": file_path, "content": xhtml, "encoding": "utf-8"})
    chapter_entries.append({"id": f"chap{idx:02}", "href": rel_href, "title": title})

    # 3) Build nav.xhtml and content.opf
    book_title = input_pdf.stem
    meta = _extract_metadata(client, input_pdf, output_epub, console)
    nav = _build_nav_xhtml(book_title, chapter_entries)
    # Build NCX for broader reader compatibility
    uid = meta.get("isbn") or "urn:uuid:00000000-0000-0000-0000-000000000000"
    ncx = _build_toc_ncx(uid, book_title, chapter_entries)
    opf = _build_content_opf(book_title, chapter_entries, meta, include_ncx=True)
    files.append({"path": "OEBPS/nav.xhtml", "content": nav, "encoding": "utf-8"})
    files.append({"path": "OEBPS/toc.ncx", "content": ncx, "encoding": "utf-8"})
    files.append({"path": "OEBPS/content.opf", "content": opf, "encoding": "utf-8"})

    return {"files": files}


def _build_manifest_by_section(
    client, input_pdf: Path, output_epub: Path, console: Console
) -> Dict[str, Any]:
    stem = output_epub.stem
    # 1) Get sections
    sections_debug = output_epub.parent / f"{stem}_sections_raw.json"
    sections = get_sections_from_pdf_verbose(
        client, str(input_pdf), console=console, debug_path=str(sections_debug)
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

    used_names: set[str] = set()
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
            debug_path=str(sec_debug),
        )
        html_fragment: str = data.get("xhtml", "")
        xhtml = _wrap_xhtml(title, html_fragment)
    base = _logical_section_basename(sec_type, idx, title, used_names)
    used_names.add(base)
    rel_href = f"{base}.xhtml"
    file_path = f"OEBPS/{rel_href}"
    files.append({"path": file_path, "content": xhtml, "encoding": "utf-8"})
    entries.append({"id": f"sec{idx:02}", "href": rel_href, "title": title, "type": sec_type})

    book_title = input_pdf.stem
    meta = _extract_metadata(client, input_pdf, output_epub, console)
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


def _extract_metadata(client, input_pdf: Path, output_epub: Path, console: Console) -> Dict[str, Any]:
    try:
        debug = output_epub.parent / f"{output_epub.stem}_metadata_raw.json"
        return get_book_metadata_verbose(client, str(input_pdf), console=console, debug_path=str(debug))
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


def _logical_section_basename(sec_type: str, idx: int, title: str, used: set[str]) -> str:
    t = (sec_type or "section").lower()
    # Common singletons
    mapping = {
        "title": "titlepage",
        "copyright": "copyright",
        "dedication": "dedication",
        "preface": "preface",
        "foreword": "foreword",
        "prologue": "prologue",
        "introduction": "introduction",
        "toc": "toc",
        "acknowledgments": "acknowledgments",
        "acknowledgements": "acknowledgments",
        "epilogue": "epilogue",
        "afterword": "afterword",
        "notes": "notes",
        "glossary": "glossary",
        "bibliography": "bibliography",
        "index": "index",
    }
    base = None
    if t == "chapter":
        base = f"chapter-{idx:02}"
    elif t == "appendix":
        base = f"appendix-{idx:02}"
    elif t in mapping:
        base = mapping[t]
        # Ensure uniqueness if multiple of same singleton appear
        if base in used:
            base = f"{base}-{idx:02}"
    else:
        # Fall back to title-based slug or type-index
        slug = _slugify(title) if title else None
        base = slug or f"section-{idx:02}"
        if base in used:
            base = f"section-{idx:02}"
    # Final collision guard
    if base in used:
        k = 2
        candidate = f"{base}-{k:02}"
        while candidate in used:
            k += 1
            candidate = f"{base}-{k:02}"
        base = candidate
    return base


def _patch_opf_in_dir(out_dir: Path, meta: Dict[str, Any], console: Console) -> None:
    try:
        # Find an OPF file (common path OEBPS/content.opf)
        opfs = list(out_dir.rglob("*.opf"))
        if not opfs:
            return
        opf_path = opfs[0]
        tree = ET.parse(opf_path)
        root = tree.getroot()
        ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        # Ensure metadata element exists
        metadata = root.find("opf:metadata", ns)
        if metadata is None:
            metadata = ET.SubElement(root, f"{{{ns['opf']}}}metadata")
        # Helper to append a dc element
        def add_dc(tag: str, text: str):
            el = ET.SubElement(metadata, f"{{{ns['dc']}}}{tag}")
            el.text = text

        title = meta.get("title")
        if title:
            add_dc("title", title)
        authors = meta.get("authors") or []
        for a in authors:
            if a:
                add_dc("creator", a)
        identifier = meta.get("isbn")
        if identifier:
            el = ET.SubElement(metadata, f"{{{ns['dc']}}}identifier", attrib={"id": "bookid"})
            el.text = identifier
            # Set unique-identifier on package if not present
            if "unique-identifier" not in root.attrib:
                root.set("unique-identifier", "bookid")
        lang = meta.get("language")
        if lang:
            add_dc("language", lang)
        publisher = meta.get("publisher")
        if publisher:
            add_dc("publisher", publisher)
        date = meta.get("date")
        if date:
            add_dc("date", date)
        description = meta.get("description")
        if description:
            add_dc("description", description)
        subjects = meta.get("subjects") or []
        for s in subjects:
            if s:
                add_dc("subject", s)
        # Also normalize manifest item hrefs to be relative to OPF dir (strip leading OEBPS/)
        manifest = root.find("opf:manifest", ns)
        if manifest is not None:
            for item in manifest.findall("opf:item", ns):
                href = item.get("href")
                if isinstance(href, str) and href.startswith("OEBPS/"):
                    item.set("href", href[len("OEBPS/"):])

        tree.write(opf_path, encoding="utf-8", xml_declaration=True)
        try:
            console.log(f"Patched metadata into OPF: {opf_path}")
        except Exception:
            pass
    except Exception:
        # Best effort; don't fail the whole build for OPF patch issues
        pass
